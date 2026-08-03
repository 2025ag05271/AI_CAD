import pytest
from pydantic import ValidationError

from catia_agent.models import (
    GetActiveDocumentAction,
    OpenCadDocumentAction,
)
from catia_agent.planner import (
    create_typed_action,
    validate_open_cad_document,
)


def test_open_catproduct_action_is_valid() -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "C:\\TestData\\"
                    "Gearbox.CATProduct"
                )
            },
        }
    )

    validate_open_cad_document(action)


def test_open_catpart_action_is_valid() -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "C:\\TestData\\Bracket.CATPart"
                )
            },
        }
    )

    validate_open_cad_document(action)


def test_open_catdrawing_action_is_valid() -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "C:\\TestData\\"
                    "Drawing.CATDrawing"
                )
            },
        }
    )

    validate_open_cad_document(action)


def test_relative_path_is_rejected() -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "TestData\\Gearbox.CATProduct"
                )
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="must be absolute",
    ):
        validate_open_cad_document(action)


def test_pdf_extension_is_rejected() -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "C:\\TestData\\Gearbox.pdf"
                )
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="Unsupported CATIA document extension",
    ):
        validate_open_cad_document(action)


def test_missing_file_path_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OpenCadDocumentAction.model_validate(
            {
                "action": "open_cad_document",
                "arguments": {},
            }
        )


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="no typed action model",
    ):
        create_typed_action(
            tool_name="delete_all_documents",
            tool_arguments={},
        )


def test_get_active_document_action_is_created() -> None:
    action = create_typed_action(
        tool_name="get_active_document",
        tool_arguments={},
    )

    assert isinstance(
        action,
        GetActiveDocumentAction,
    )

    assert action.action == "get_active_document"
    assert action.arguments.model_dump() == {}