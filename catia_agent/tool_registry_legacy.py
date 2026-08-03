from typing import Any


OPEN_CAD_DOCUMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open_cad_document",
        "description": (
            "Open an existing CATIA CAD document using an "
            "absolute Windows file path. Use this tool only "
            "for CATPart, CATProduct, or CATDrawing files."
        ),
        "parameters": {
            "type": "object",
            "required": [
                "filePath",
            ],
            "properties": {
                "filePath": {
                    "type": "string",
                    "description": (
                        "Absolute Windows path of the CATIA "
                        "document to open."
                    ),
                }
            },
        },
    },
}


GET_ACTIVE_DOCUMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_active_document",
        "description": (
            "Get information about the document that is "
            "currently active in CATIA. Use this tool when "
            "the user asks which CATIA document is active, "
            "current, selected, or in focus."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


CATIA_TOOLS: list[dict[str, Any]] = [
    OPEN_CAD_DOCUMENT_TOOL,
    GET_ACTIVE_DOCUMENT_TOOL,
]


SUPPORTED_TOOL_NAMES = {
    tool["function"]["name"]
    for tool in CATIA_TOOLS
}