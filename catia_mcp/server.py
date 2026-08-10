from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any, Literal

from fastmcp import FastMCP
from catia_agent.utils import (
    validate_download_cad_inputs,
    validate_start_model_inputs,
)


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
    documentType: str | None = None,
    partTitle: str | None = None,
    savePath: str | None = None,
) -> dict[str, Any]:
    """
    Prepare an action to download a CAD file from the PLM system.

    Enovia and PLM are the same system. Requests that imply download,
    fetch, retrieve, get, obtain, or locate a CAD document from PLM or
    Enovia should use this tool.

    Supported input shapes:

        1) PLM object shape
        - partNumber + partRevision
        - Examples:
            674089800A + 00
            X670830400C + 00-02
        - For X-prefixed part numbers, revisions are typically
            hyphenated (for example 00-01 or 00-02).

        2) CAD identifier shape
        - partNumber + partRevision
        - Examples:
            6328629 + CAD000
            6328629 + CAD002
        - documentType is optional unless the user explicitly
            mentions a type. If mentioned, populate it.
        - Normalize:
            part -> CATPart
            cat part -> CATPart
            product -> CATProduct
            cat product -> CATProduct
            drawing -> CATDrawing
            cat drawing -> CATDrawing

        3) Title lookup shape
        - partTitle only
        - Examples:
            anchor plate
            retractor
        - Do not include partNumber or partRevision.

        Save location:
        - savePath is optional.
        - If omitted, default is C:\\Temp.
        - If provided, it must be an absolute Windows path.

    Examples:

        User: "download cat part 6328629 CAD000"
        Arguments:
        partNumber = "6328629"
        partRevision = "CAD000"
        documentType = "CATPart"

        User: "fetch X670830400C revision 00-02 from enovia"
        Arguments:
        partNumber = "X670830400C"
        partRevision = "00-02"

        User: "download anchor plate"
        Arguments:
        partTitle = "anchor plate"

    General rules:
        - Do not invent partNumber, partRevision, documentType, or partTitle.
        - Use user-provided values or trusted conversation context.
        - If required identifiers are missing or ambiguous,
        use request_clarification instead.

    This tool only prepares the request. The external WPF C# executor
    classifies the shape and calls the correct PLM/TIF API.
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

    normalized_document_type, normalized_save_path = (
        validate_download_cad_inputs(
            part_number=normalized_part_number,
            part_revision=normalized_part_revision,
            part_title=normalized_part_title,
            document_type=documentType,
            save_path=savePath,
        )
    )

    return create_executor_action(
        action="download_cad",
        arguments={
            "partNumber": normalized_part_number,
            "partRevision": normalized_part_revision,
            "documentType": normalized_document_type,
            "partTitle": normalized_part_title,
            "savePath": normalized_save_path,
        },
    )


@mcp.tool
def start_model(
    *,
    modelType: str,
    outsystemsId: str | None = None,
    afisNumber: str | None = None,
    customName: str | None = None,
    partNumber: str | None = None,
    partRevision: str | None = None,
    savePath: str | None = None,
) -> dict[str, Any]:
    """
    Prepare an action to start an approved CATIA start model.

    Supported start model families:

    - SAB (also called side airbag or airbag or softcover or hardcover or wireharness): requires outsystemsId
    - DU (also called driver unit or steering wheel): requires outsystemsId
    - SEATBELT (also called buckle): requires partNumber + partRevision

    IC start model is currently not supported and must be rejected.

    ID extraction guidance:
    - outsystemsId is the required identifier.
    - The user may refer to this identifier as Outsystems ID, AO ID,
      or simply ID.
    - Use only the ID explicitly supplied by the user.

    Args:
        outsystemsId: Outsystems/AO identifier for SAB or DU requests.
        afisNumber: AFIS number for only SAB or DU requests. This is mandatory arg.
        customName: Custom assembly name for only SAB or DU requests. This is optional.
        partNumber: Seatbelt start-model part number.
        partRevision: Seatbelt start-model revision.
        modelType: Requested start model family or alias.
        Save location:
            - savePath is optional.
            - This applies for all start model families.
            - If omitted, default is C:\\Temp.
            - If provided, it must be an absolute Windows path.
    """

    (
        normalized_outsystems_id,
        normalized_part_number,
        normalized_part_revision,
        normalized_afis_number,
        normalized_custom_name,
        normalized_model_type,
        normalized_save_path
    ) = validate_start_model_inputs(
        outsystems_id=outsystemsId,
        model_type=modelType,
        part_number=partNumber,
        afis_number=afisNumber,
        custom_name=customName,
        part_revision=partRevision,
        save_path=savePath,
    )

    return create_executor_action(
        action="start_model",
        arguments={
            "outsystemsId": normalized_outsystems_id,
            "partNumber": normalized_part_number,
            "afisNumber": normalized_afis_number,
            "customName": normalized_custom_name,
            "partRevision": normalized_part_revision,
            "modelType": normalized_model_type,
            "savePath": normalized_save_path,
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