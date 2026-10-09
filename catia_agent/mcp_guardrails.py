"""
Guardrails applied to every tool catalog received from the CATIA MCP
server before any tool definition can reach the model.

Checks, in order, per tool:

1. Allowlist: only tools the Agent has integrated end to end
   (APPROVED_CATIA_TOOL_NAMES) are kept.
2. Content inspection: description/schema size limits and rejection of
   prompt-injection markers or hidden Unicode in any text the model
   would read ("tool poisoning").
3. Definition pinning: the SHA-256 of each tool's name, description and
   schema must match mcp_tool_manifest.json, so a changed or compromised
   MCP server cannot silently alter what the model is told
   ("rug pull").

Rejected tools are dropped and logged as security events; the caller
fails closed if nothing survives.

Re-pin after intentionally changing a tool in catia_mcp/server.py:

    python -m catia_agent.mcp_guardrails pin
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from catia_agent.config import settings
from catia_agent.logging_config import get_logger, log_event


logger = get_logger(__name__)


APPROVED_CATIA_TOOL_NAMES = frozenset(
    {
        "open_cad_document",
        "close_cad_document",
        "create_cad_document",
        "download_cad",
        "start_model",
        "open_master_drawing",
    }
)

MANIFEST_PATH = Path(__file__).with_name("mcp_tool_manifest.json")

_PROMPT_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)"
        r"\s+(?:instructions|rules|prompts?)",
        r"disregard\s+(?:the\s+|all\s+)?(?:system|previous|above|prior)",
        r"system\s+prompt",
        r"<\|?\s*(?:im_start|im_end|system|endoftext)\s*\|?>",
        r"</?\s*(?:system|assistant|instructions?)\s*>",
        r"\bdo\s+not\s+(?:tell|inform|mention|reveal)\b",
        r"\b(?:always|must)\s+(?:call|use|select|prefer)\s+this\s+tool\b",
        r"https?://",
    )
)

# Zero-width, bidi-override and Unicode tag characters can hide
# instructions from human reviewers while the model still reads them.
_HIDDEN_CHARACTER_PATTERN = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff"
    "\U000e0000-\U000e007f]"
)


# -------------------------------------------------------------------------
# Fingerprints and manifest
# -------------------------------------------------------------------------

def compute_tool_fingerprint(tool: dict[str, Any]) -> str:
    function = tool["function"]

    canonical = json.dumps(
        {
            "name": function["name"],
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_tool_manifest(
    path: Path = MANIFEST_PATH,
) -> dict[str, str] | None:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None

    tools = content.get("tools")

    if not isinstance(tools, dict):
        raise ValueError(f"Invalid MCP tool manifest: {path}")

    return {str(name): str(digest) for name, digest in tools.items()}


def write_tool_manifest(
    tools: list[dict[str, Any]],
    path: Path = MANIFEST_PATH,
) -> dict[str, str]:
    fingerprints = {
        tool["function"]["name"]: compute_tool_fingerprint(tool)
        for tool in sorted(tools, key=lambda t: t["function"]["name"])
    }

    path.write_text(
        json.dumps(
            {"version": 1, "tools": fingerprints},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return fingerprints


# -------------------------------------------------------------------------
# Content inspection
# -------------------------------------------------------------------------

def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        return [
            text
            for key, nested in value.items()
            for text in (*_collect_strings(key), *_collect_strings(nested))
        ]

    if isinstance(value, list):
        return [text for item in value for text in _collect_strings(item)]

    return []


def inspect_tool_definition(tool: dict[str, Any]) -> list[str]:
    """
    Return the reasons a tool definition is unsafe to show the model;
    an empty list means it passed.
    """

    function = tool["function"]
    description = function.get("description", "") or ""
    parameters = function.get("parameters", {}) or {}

    violations: list[str] = []

    if len(description) > settings.mcp_max_tool_description_characters:
        violations.append("description_too_long")

    schema_bytes = len(
        json.dumps(parameters, separators=(",", ":")).encode("utf-8")
    )

    if schema_bytes > settings.mcp_max_tool_schema_bytes:
        violations.append("schema_too_large")

    if len(parameters.get("properties", {})) > settings.mcp_max_tool_properties:
        violations.append("too_many_properties")

    texts = [function["name"], description, *_collect_strings(parameters)]

    if any(_HIDDEN_CHARACTER_PATTERN.search(text) for text in texts):
        violations.append("hidden_unicode_characters")

    for pattern in _PROMPT_INJECTION_PATTERNS:
        if any(pattern.search(text) for text in texts):
            violations.append(f"suspicious_text:{pattern.pattern}")

    return violations


# -------------------------------------------------------------------------
# Catalog filter
# -------------------------------------------------------------------------

def _reject(tool_name: str, reason: str, **fields: Any) -> None:
    log_event(
        logger,
        "mcp_tool_rejected",
        level=logging.ERROR,
        securityEvent=True,
        toolName=tool_name,
        reason=reason,
        **fields,
    )


def apply_tool_guardrails(
    tools: list[dict[str, Any]],
    *,
    manifest: dict[str, str] | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> list[dict[str, Any]]:
    """
    Return only the tools that pass every guardrail.
    """

    pinning_mode = settings.mcp_tool_pinning_mode

    if pinning_mode != "off" and manifest is None:
        manifest = load_tool_manifest(manifest_path)

        if manifest is None:
            log_event(
                logger,
                "mcp_tool_manifest_missing",
                level=(
                    logging.ERROR
                    if pinning_mode == "enforce"
                    else logging.WARNING
                ),
                securityEvent=True,
                manifestPath=str(manifest_path),
            )
            manifest = {}

    if len(tools) > settings.mcp_max_tool_count:
        log_event(
            logger,
            "mcp_tool_catalog_too_large",
            level=logging.ERROR,
            securityEvent=True,
            toolCount=len(tools),
            maxToolCount=settings.mcp_max_tool_count,
        )
        return []

    accepted: list[dict[str, Any]] = []

    for tool in tools:
        tool_name = tool["function"]["name"]

        if tool_name not in APPROVED_CATIA_TOOL_NAMES:
            _reject(tool_name, "not_approved")
            continue

        violations = inspect_tool_definition(tool)

        if violations:
            _reject(tool_name, "unsafe_definition", violations=violations)
            continue

        if pinning_mode != "off":
            expected = (manifest or {}).get(tool_name)
            actual = compute_tool_fingerprint(tool)

            if expected != actual:
                if pinning_mode == "enforce":
                    _reject(
                        tool_name,
                        "definition_not_pinned"
                        if expected is None
                        else "definition_changed",
                        expectedFingerprint=expected,
                        actualFingerprint=actual,
                    )
                    continue

                log_event(
                    logger,
                    "mcp_tool_definition_mismatch",
                    level=logging.WARNING,
                    securityEvent=True,
                    toolName=tool_name,
                    expectedFingerprint=expected,
                    actualFingerprint=actual,
                )

        accepted.append(tool)

    log_event(
        logger,
        "mcp_tool_guardrails_applied",
        pinningMode=pinning_mode,
        receivedCount=len(tools),
        acceptedCount=len(accepted),
    )

    return accepted


# -------------------------------------------------------------------------
# CLI: python -m catia_agent.mcp_guardrails pin
# -------------------------------------------------------------------------

async def load_tools_from_server_source() -> list[dict[str, Any]]:
    """
    List tools in-process from catia_mcp/server.py (no network), as the
    trusted source for pinning.
    """

    from fastmcp import Client

    from catia_agent.mcp_tools import convert_mcp_tool_to_ollama
    from catia_mcp.server import mcp

    async with Client(mcp) as client:
        raw_tools = await client.list_tools()

    return [convert_mcp_tool_to_ollama(tool) for tool in raw_tools]


def _pin() -> int:
    tools = asyncio.run(load_tools_from_server_source())

    problems = {
        tool["function"]["name"]: violations
        for tool in tools
        if (violations := inspect_tool_definition(tool))
    }

    unapproved = sorted(
        tool["function"]["name"]
        for tool in tools
        if tool["function"]["name"] not in APPROVED_CATIA_TOOL_NAMES
    )

    if problems or unapproved:
        print(
            "Refusing to pin. Unsafe: "
            f"{problems or 'none'}; not approved: {unapproved or 'none'}",
            file=sys.stderr,
        )
        return 1

    fingerprints = write_tool_manifest(tools)

    for name, digest in fingerprints.items():
        print(f"{name}: {digest}")

    print(f"Wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["pin"]:
        print("Usage: python -m catia_agent.mcp_guardrails pin", file=sys.stderr)
        sys.exit(2)

    sys.exit(_pin())
