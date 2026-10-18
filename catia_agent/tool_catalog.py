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
                "Use partNumber with an optional partRevision when the "
                "user provides an identifier. If the user gives only a "
                "title or product name, place that phrase in partTitle; "
                "the application will resolve its PLM drawing link. "
                "Never invent a part number, revision, title, or URL."
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
                    "partTitle": {
                        "type": ["string", "null"],
                        "description": "Title or product name explicitly supplied by the user when no part number is given.",
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