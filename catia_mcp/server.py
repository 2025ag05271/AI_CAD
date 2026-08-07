from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any, Literal

from fastmcp import FastMCP


# -------------------------------------------------------------------------
# MCP server
# -------------------------------------------------------------------------

mcp = FastMCP(
    name="CATIA Tools",
    instructions=(
        "This server exposes approved CATIA actions for discovery by "
        "the CATIA Agent. Tools prepare typed action requests for an "
        "external WPF C# executor. This MCP server does not access "
        "CATIA, execute COM operations, or modify CAD documents."
    ),
)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

ALLOWED_DOCUMENT_EXTENSIONS = frozenset(
    {
        ".catpart",
        ".catproduct",
        ".catdrawing",
    }
)

DocumentType = Literal[
    "CATProduct",
    "CATPart",
    "CATDrawing",
]

CloseTargetMode = Literal[
    "active",
    "named",
    "all",
]


# -------------------------------------------------------------------------
# Shared validation
# -------------------------------------------------------------------------

def validate_windows_document_path(
    file_path: str,
) -> str:
    """
    Validate and normalize an absolute Windows CATIA document path.

    File existence and approved-directory checks remain the
    responsibility of the external C# executor because it owns access
    to the Windows file system and CATIA.
    """

    normalized_path = file_path.strip()

    if not normalized_path:
        raise ValueError(
            "The CATIA document path must not be empty."
        )

    path = PureWindowsPath(normalized_path)

    if not path.is_absolute():
        raise ValueError(
            "The CATIA document path must be an absolute "
            "Windows path."
        )

    extension = path.suffix.lower()

    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        displayed_extension = (
            path.suffix
            if path.suffix
            else "[missing extension]"
        )

        raise ValueError(
            "Unsupported CATIA document extension: "
            f"{displayed_extension}"
        )

    return str(path)


def normalize_document_name(
    document_name: str,
) -> str:
    """
    Validate a document name supplied to a named-document operation.
    """

    normalized_name = document_name.strip()

    if not normalized_name:
        raise ValueError(
            "The CATIA document name must not be empty."
        )

    if len(normalized_name) > 260:
        raise ValueError(
            "The CATIA document name is too long."
        )

    return normalized_name


def create_executor_action(
    *,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create the standard action-preparation envelope returned by MCP
    tools.

    The result describes a proposed external operation. It does not
    indicate that CATIA execution has occurred.
    """

    return {
        "status": "action_prepared",
        "action": action,
        "arguments": arguments,
        "executor": "wpf_csharp",
        "executed": False,
    }


# -------------------------------------------------------------------------
# CATIA document tools
# -------------------------------------------------------------------------

@mcp.tool
def open_cad_document(
    filePath: str,
) -> dict[str, Any]:
    """
    Prepare an action to open an existing CATIA document.

    Use this tool only when the user asks to open a CATPart,
    CATProduct, or CATDrawing document.

    The file path must be an absolute Windows path. Do not invent or
    infer a path that was not supplied by the user or resolved from
    trusted conversation context.

    This tool prepares an action for the external WPF C# executor. It
    does not access the file system, start CATIA, or open the document.

    Args:
        filePath: Absolute Windows path of the CATIA document.
    """

    validated_path = validate_windows_document_path(
        filePath
    )

    return create_executor_action(
        action="open_cad_document",
        arguments={
            "filePath": validated_path,
        },
    )


@mcp.tool
def get_active_document() -> dict[str, Any]:
    """
    Prepare an action to retrieve the currently active CATIA document.

    Use this tool when the user asks which CATIA document is current,
    active, selected, or in focus.

    Use the external executor even if a CATIA context snapshot was
    supplied because the current CATIA state may have changed.

    This tool does not query CATIA directly.
    """

    return create_executor_action(
        action="get_active_document",
        arguments={},
    )


@mcp.tool
def create_cad_document(
    documentType: DocumentType,
) -> dict[str, Any]:
    """
    Prepare an action to create a new, unsaved CATIA document.

    Map user terminology as follows:

    - part to CATPart
    - product or assembly to CATProduct
    - drawing to CATDrawing

    Creating a document does not save it to disk. Saving is a separate
    operation and must use a separately approved tool when available.

    This tool prepares an action for the external WPF C# executor. It
    does not create the document directly.

    Args:
        documentType: Type of CATIA document to create.
    """

    return create_executor_action(
        action="create_cad_document",
        arguments={
            "documentType": documentType,
        },
    )


@mcp.tool
def close_cad_document(
    targetMode: CloseTargetMode,
    documentName: str | None = None,
) -> dict[str, Any]:
    """
    Prepare an action to close one or more open CATIA documents.

    Use targetMode="active" when the user explicitly refers to the
    active or current document. It may also be used when the user asks
    to close the open document and trusted CATIA context confirms that
    exactly one document is open.

    Use targetMode="named" when the user specifies or unambiguously
    identifies a document. documentName is required in this mode.

    Use targetMode="all" only when the user explicitly asks to close
    every open CATIA document.

    If the requested document cannot be identified unambiguously, the
    orchestrator must ask for clarification instead of calling this
    tool.

    This action does not specify save behavior. The C# executor must
    reject the operation if closing the document could discard unsaved
    changes without an approved save or discard policy.

    Args:
        targetMode: Whether to close the active, named, or all
            documents.
        documentName: Exact document name when targetMode is named.
    """

    normalized_document_name: str | None = None

    if targetMode == "named":
        if documentName is None:
            raise ValueError(
                "documentName is required when targetMode "
                "is named."
            )

        normalized_document_name = (
            normalize_document_name(
                documentName
            )
        )

    return create_executor_action(
        action="close_cad_document",
        arguments={
            "targetMode": targetMode,
            "documentName": (
                normalized_document_name
            ),
        },
    )


# -------------------------------------------------------------------------
# Server entry point
# -------------------------------------------------------------------------

def run_server() -> None:
    """
    Run the local CATIA MCP HTTP server.
    """

    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=8001,
    )


if __name__ == "__main__":
    run_server()