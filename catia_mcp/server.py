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


# Temporarily unpublished. Uncomment @mcp.tool and re-add
# "get_active_document" to APPROVED_CATIA_TOOL_NAMES (planner.py) and
# TERMINAL_READ_ONLY_TOOLS (graph.py) to re-enable.
# @mcp.tool
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


@mcp.tool
def download_cad(
    partNumber: str | None = None,
    partRevision: str | None = None,
    documentType: DocumentType | None = None,
    partTitle: str | None = None,
) -> dict[str, Any]:
    """
    Prepare an action to download a CAD file from the PLM system.

    Enovia and PLM refer to the same system. Any user request to
    download, fetch, retrieve, or get a CAD document from PLM or
    Enovia must map to this tool.

    The tool accepts three valid input shapes. Exactly one shape must
    be used per call.

    Shape 1 - PLM object identifier:
      partNumber examples: "674089800A", "X670830400C".
      partRevision examples: "00", "01", or "00-02" (the hyphenated
      form is only valid when partNumber begins with "X").

    Shape 2 - CAD identifier:
      partNumber example: "6740898".
      partRevision examples: "CAD000", "CAD002".
      documentType is optional here.

    Shape 3 - Title lookup:
      partTitle describing the CAD by name, for example
      "anchor plate" or "retractor". Do not include partNumber or
      partRevision when using this shape.

    documentType, when provided, must be CATPart, CATProduct, or
    CATDrawing. If the user says "part", "product", or "drawing",
    normalize to CATPart, CATProduct, or CATDrawing respectively.

    Do not invent identifiers, revisions, or titles that were not
    supplied by the user or resolvable from trusted conversation
    context. If the required identifiers are unclear or missing, call
    request_clarification instead of this tool.

    This tool prepares an action for the external WPF C# executor.
    The executor classifies which shape was supplied and calls the
    correct PLM API to resolve and download the file. This tool does
    not access PLM, Enovia, or the file system directly.

    Args:
        partNumber: PLM or CAD part identifier. Required together
            with partRevision for shapes 1 and 2.
        partRevision: PLM or CAD revision. Required together with
            partNumber for shapes 1 and 2.
        documentType: Optional CAD document type. Only meaningful
            for shape 2.
        partTitle: Human-readable CAD title. Required and used alone
            for shape 3.
    """

    normalized_part_number: str | None = None
    normalized_part_revision: str | None = None
    normalized_part_title: str | None = None

    if partNumber is not None:
        normalized_part_number = partNumber.strip() or None

    if partRevision is not None:
        normalized_part_revision = partRevision.strip() or None

    if partTitle is not None:
        normalized_part_title = partTitle.strip() or None

    has_number = normalized_part_number is not None
    has_revision = normalized_part_revision is not None
    has_title = normalized_part_title is not None

    if has_title and (has_number or has_revision):
        raise ValueError(
            "partTitle cannot be combined with partNumber or "
            "partRevision."
        )

    if not has_title:
        if has_number and not has_revision:
            raise ValueError(
                "partRevision is required when partNumber is "
                "provided."
            )

        if has_revision and not has_number:
            raise ValueError(
                "partNumber is required when partRevision is "
                "provided."
            )

        if not has_number:
            raise ValueError(
                "download_cad requires either both partNumber "
                "and partRevision, or partTitle."
            )

    if (
        normalized_part_number is not None
        and len(normalized_part_number) > 50
    ):
        raise ValueError(
            "partNumber is too long."
        )

    if (
        normalized_part_revision is not None
        and len(normalized_part_revision) > 20
    ):
        raise ValueError(
            "partRevision is too long."
        )

    if normalized_part_title is not None:
        if len(normalized_part_title) < 2:
            raise ValueError(
                "partTitle must be at least 2 characters."
            )

        if len(normalized_part_title) > 200:
            raise ValueError(
                "partTitle is too long."
            )

    return create_executor_action(
        action="download_cad",
        arguments={
            "partNumber": normalized_part_number,
            "partRevision": normalized_part_revision,
            "documentType": documentType,
            "partTitle": normalized_part_title,
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