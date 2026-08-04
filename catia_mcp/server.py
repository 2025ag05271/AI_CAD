from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP


mcp = FastMCP(
    name="CATIA Tools",
    instructions=(
        "This server exposes approved CATIA actions. "
        "The tools prepare typed action requests for an external "
        "WPF C# executor. The MCP server does not access CATIA "
        "or execute COM operations directly."
    ),
)


ALLOWED_DOCUMENT_EXTENSIONS = {
    ".catpart",
    ".catproduct",
    ".catdrawing",
}


@mcp.tool
def open_cad_document(
    filePath: str,
) -> dict[str, Any]:
    """
    Prepare an action to open an existing CATIA document.

    Use this tool when the user explicitly requests opening a
    CATPart, CATProduct, or CATDrawing document.

    The filePath must be an absolute Windows file path.
    This tool does not open CATIA itself. It returns a validated
    action for execution by the external WPF C# CATIA executor.

    Args:
        filePath: Absolute Windows path of the CATIA document.
    """

    path = Path(filePath)

    if not path.is_absolute():
        raise ValueError(
            "The CATIA document path must be absolute."
        )

    extension = path.suffix.lower()

    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValueError(
            "Unsupported CATIA document extension: "
            f"{path.suffix or '[missing extension]'}"
        )

    return {
        "status": "action_prepared",
        "action": "open_cad_document",
        "arguments": {
            "filePath": filePath,
        },
        "executor": "wpf_csharp",
        "executed": False,
    }


@mcp.tool
def get_active_document() -> dict[str, Any]:
    """
    Prepare an action to retrieve the active CATIA document.

    Use this tool when the user asks which CATIA document is
    active, current, selected, or in focus.

    This tool does not query CATIA itself. It returns a typed
    action for execution by the external WPF C# CATIA executor.
    """

    return {
        "status": "action_prepared",
        "action": "get_active_document",
        "arguments": {},
        "executor": "wpf_csharp",
        "executed": False,
    }


@mcp.tool
def create_cad_document(
    documentType: Literal["CATProduct", "CATPart", "CATDrawing"],
) -> dict[str, Any]:
    """
    Prepare an action to create a new CATIA document.

    Use this tool when the user asks to create, start, or make a new
    CATIA part, product, assembly, or drawing document.

    Map "part" to CATPart, "product" or "assembly" to CATProduct, and
    "drawing" to CATDrawing.

    This tool does not require a file path. The new document is created
    unsaved in CATIA; saving it to disk is a separate action.

    This tool does not create the document itself. It returns a validated
    action for execution by the external WPF C# CATIA executor.

    Args:
        documentType: The type of CATIA document to create.
    """

    return {
        "status": "action_prepared",
        "action": "create_cad_document",
        "arguments": {
            "documentType": documentType,
        },
        "executor": "wpf_csharp",
        "executed": False,
    }


@mcp.tool
def close_cad_document(
    targetMode: Literal["active", "named", "all"],
    documentName: str | None = None,
) -> dict[str, Any]:
    """
    Prepare an action to close one or more open CATIA documents.

    Use targetMode="active" when the user refers to the active,
    current, or currently focused CATIA document. Also use
    targetMode="active" when the user asks to close the open
    document and exactly one CATIA document is currently open.

    Use targetMode="named" when the user explicitly provides or
    resolves a document name. documentName is required in that
    case; preserve it exactly as provided or resolved.

    Use targetMode="all" only when the user explicitly asks to
    close all open CATIA documents.

    Do not use this tool when the user merely says "close a
    document" or "close document" without identifying a document,
    or when more than one document is open and none is specified.
    Ask the user which document should be closed instead.

    This tool prepares an action for the external WPF C# executor.
    It does not access CATIA directly.
    """

    if targetMode == "named":
        if documentName is None or not documentName.strip():
            raise ValueError(
                "documentName is required when "
                "targetMode is named."
            )

        documentName = documentName.strip()

    elif documentName is not None:
        documentName = None

    return {
        "status": "action_prepared",
        "action": "close_cad_document",
        "arguments": {
            "targetMode": targetMode,
            "documentName": documentName,
        },
        "executor": "wpf_csharp",
        "executed": False,
    }

@mcp.tool
def request_clarification(
    question: str,
    missingField: str,
) -> dict[str, Any]:
    """
    Ask the user for information required before a CATIA action
    can be prepared.

    Use this tool when the request is potentially supported but
    required information is missing or ambiguous.

    For example, if the user says only "close document", ask
    which open document should be closed.

    This tool is handled by the Python Agent workflow. It must
    never be sent to the C# CATIA executor.
    """

    if not question.strip():
        raise ValueError(
            "A clarification question is required."
        )

    if not missingField.strip():
        raise ValueError(
            "The missing field name is required."
        )

    return {
        "status": "clarification_required",
        "question": question.strip(),
        "missingField": missingField.strip(),
    }


@mcp.tool
def route_to_general_chat() -> dict[str, Any]:
    """
    Signal that this message does not require a CATIA action.

    Use this tool for greetings, general questions, requests for
    explanation, or anything else unrelated to the CATIA tools above.

    Do not attempt to answer the user's question yourself. Call this
    tool instead and let a separate general-purpose assistant respond.

    This tool is handled by the Python Agent workflow. It must never
    be sent to the C# CATIA executor.
    """

    return {
        "status": "non_agentic",
        "action": "route_to_general_chat",
        "arguments": {},
        "executor": "external_chat_llm",
        "executed": False,
    }


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=8001,
    )