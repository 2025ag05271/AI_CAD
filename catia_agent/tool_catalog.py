from __future__ import annotations

from copy import deepcopy
from typing import Any


_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_master_drawing",
            "description": (
                "Prepare opening the master drawing for a part. "
                "Use only the part number and optional revision stated "
                "by the user. If the part number is missing, ask for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "partNumber": {
                        "type": ["string", "null"],
                        "description": "Part number whose master drawing to open.",
                    },
                    "partRevision": {
                        "type": ["string", "null"],
                        "description": "Optional part revision explicitly given by the user.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


async def get_model_tools(
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Return the built-in executor tool catalog without network discovery."""

    del force_refresh
    return deepcopy(_TOOL_DEFINITIONS)