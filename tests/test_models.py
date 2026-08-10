import pytest
from pydantic import ValidationError

from catia_agent.models import (
    ActionExecutionResultRequest,
    AgentRunRequest,
    CatiaContext,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CreateCadDocumentAction,
    DownloadCadAction,
    GetActiveDocumentAction,
    NonAgenticResult,
    OpenCadDocumentAction,
    RequestClarificationAction,
    RouteToGeneralChatAction,
    StartModelAction,
    UnsupportedRequestResult,
)


def test_open_cad_document_action_requires_file_path() -> None:
    with pytest.raises(ValidationError):
        OpenCadDocumentAction.model_validate(
            {
                "action": "open_cad_document",
                "arguments": {},
            }
        )


def test_get_active_document_action_ignores_extra_arguments() -> None:
    action = GetActiveDocumentAction.model_validate(
        {
            "action": "get_active_document",
            "arguments": {},
        }
    )

    assert action.action == "get_active_document"


def test_close_cad_document_action_accepts_named_mode() -> None:
    action = CloseCadDocumentAction.model_validate(
        {
            "action": "close_cad_document",
            "arguments": {
                "targetMode": "named",
                "documentName": "Gearbox.CATProduct",
            },
        }
    )

    assert action.arguments.targetMode == "named"
    assert action.arguments.documentName == "Gearbox.CATProduct"


def test_close_cad_document_action_rejects_unknown_target_mode() -> None:
    with pytest.raises(ValidationError):
        CloseCadDocumentAction.model_validate(
            {
                "action": "close_cad_document",
                "arguments": {
                    "targetMode": "everything",
                },
            }
        )


@pytest.mark.parametrize(
    "document_type",
    ["CATPart", "CATProduct", "CATDrawing"],
)
def test_create_cad_document_action_accepts_supported_types(
    document_type: str,
) -> None:
    action = CreateCadDocumentAction.model_validate(
        {
            "action": "create_cad_document",
            "arguments": {
                "documentType": document_type,
            },
        }
    )

    assert action.arguments.documentType == document_type


def test_create_cad_document_action_rejects_unsupported_type() -> None:
    with pytest.raises(ValidationError):
        CreateCadDocumentAction.model_validate(
            {
                "action": "create_cad_document",
                "arguments": {
                    "documentType": "CATAnalysis",
                },
            }
        )


def test_download_cad_accepts_plm_number_and_revision() -> None:
    action = DownloadCadAction.model_validate(
        {
            "action": "download_cad",
            "arguments": {
                "partNumber": "674089800A",
                "partRevision": "00",
            },
        }
    )

    assert action.arguments.partNumber == "674089800A"
    assert action.arguments.partRevision == "00"
    assert action.arguments.savePath == "C:\\Temp"


def test_download_cad_accepts_x_prefixed_hyphen_revision() -> None:
    action = DownloadCadAction.model_validate(
        {
            "action": "download_cad",
            "arguments": {
                "partNumber": "X670830400C",
                "partRevision": "00-02",
            },
        }
    )

    assert action.arguments.partNumber == "X670830400C"
    assert action.arguments.partRevision == "00-02"


def test_download_cad_rejects_x_prefixed_non_hyphen_revision() -> None:
    with pytest.raises(ValidationError, match="hyphenated form"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "X670830400C",
                    "partRevision": "00",
                },
            }
        )


def test_download_cad_accepts_cad_number_revision_and_type_alias() -> None:
    action = DownloadCadAction.model_validate(
        {
            "action": "download_cad",
            "arguments": {
                "partNumber": "6740898",
                "partRevision": "CAD002",
                "documentType": "product",
            },
        }
    )

    assert action.arguments.documentType == "CATProduct"


def test_download_cad_accepts_explicit_save_path() -> None:
    action = DownloadCadAction.model_validate(
        {
            "action": "download_cad",
            "arguments": {
                "partNumber": "6740898",
                "partRevision": "CAD002",
                "savePath": "C:\\Downloads\\CATIA",
            },
        }
    )

    assert action.arguments.savePath == "C:\\Downloads\\CATIA"


def test_download_cad_rejects_relative_save_path() -> None:
    with pytest.raises(ValidationError, match="absolute Windows path"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "6740898",
                    "partRevision": "CAD002",
                    "savePath": "Downloads\\CATIA",
                },
            }
        )


def test_download_cad_accepts_title_lookup_shape() -> None:
    action = DownloadCadAction.model_validate(
        {
            "action": "download_cad",
            "arguments": {
                "partTitle": "anchor plate",
            },
        }
    )

    assert action.arguments.partTitle == "anchor plate"


def test_download_cad_rejects_missing_revision_with_number() -> None:
    with pytest.raises(ValidationError, match="partRevision is required"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "674089800A",
                },
            }
        )


def test_download_cad_rejects_hyphen_revision_for_non_x_part() -> None:
    with pytest.raises(ValidationError, match="PLM part numbers require"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "674089800A",
                    "partRevision": "00-02",
                },
            }
        )


def test_download_cad_rejects_bad_cad_revision() -> None:
    with pytest.raises(ValidationError, match="CAD part numbers require"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "6740898",
                    "partRevision": "00",
                },
            }
        )


def test_download_cad_rejects_title_combined_with_number_shape() -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        DownloadCadAction.model_validate(
            {
                "action": "download_cad",
                "arguments": {
                    "partNumber": "6740898",
                    "partRevision": "CAD002",
                    "partTitle": "retractor",
                },
            }
        )


def test_start_model_accepts_side_airbag_alias() -> None:
    action = StartModelAction.model_validate(
        {
            "action": "start_model",
            "arguments": {
                "outsystemsId": "2245",
                "afisNumber": "AFIS-1001",
                "modelType": "side airbag",
            },
        }
    )

    assert action.arguments.outsystemsId == "2245"
    assert action.arguments.afisNumber == "AFIS-1001"
    assert action.arguments.modelType == "SAB"


def test_start_model_accepts_steering_wheel_alias() -> None:
    action = StartModelAction.model_validate(
        {
            "action": "start_model",
            "arguments": {
                "outsystemsId": "AO-2245",
                "afisNumber": "AFIS-2201",
                "modelType": "steering wheel",
            },
        }
    )

    assert action.arguments.afisNumber == "AFIS-2201"
    assert action.arguments.modelType == "DU"


def test_start_model_accepts_seatbelt_part_shape() -> None:
    action = StartModelAction.model_validate(
        {
            "action": "start_model",
            "arguments": {
                "partNumber": "6687874100A",
                "partRevision": "00",
                "modelType": "seatbelt",
            },
        }
    )

    assert action.arguments.modelType == "SEATBELT"
    assert action.arguments.partNumber == "6687874100A"
    assert action.arguments.partRevision == "00"
    assert action.arguments.outsystemsId is None


def test_start_model_accepts_9_digit_seatbelt_part_shape() -> None:
    action = StartModelAction.model_validate(
        {
            "action": "start_model",
            "arguments": {
                "partNumber": "674089800A",
                "partRevision": "01",
                "modelType": "seatbelt",
            },
        }
    )

    assert action.arguments.modelType == "SEATBELT"
    assert action.arguments.partNumber == "674089800A"
    assert action.arguments.partRevision == "01"


def test_start_model_accepts_x_prefixed_seatbelt_part_shape() -> None:
    action = StartModelAction.model_validate(
        {
            "action": "start_model",
            "arguments": {
                "partNumber": "X6565654500A",
                "partRevision": "00-01",
                "modelType": "buckle",
            },
        }
    )

    assert action.arguments.modelType == "SEATBELT"
    assert action.arguments.partRevision == "00-01"


def test_start_model_rejects_seatbelt_without_part_number() -> None:
    with pytest.raises(ValidationError, match="partNumber and partRevision"):
        StartModelAction.model_validate(
            {
                "action": "start_model",
                "arguments": {
                    "modelType": "seatbelt",
                },
            }
        )


def test_start_model_rejects_x_prefixed_seatbelt_bad_revision() -> None:
    with pytest.raises(ValidationError, match="hyphenated form"):
        StartModelAction.model_validate(
            {
                "action": "start_model",
                "arguments": {
                    "partNumber": "X6565654500A",
                    "partRevision": "00",
                    "modelType": "seatbelt",
                },
            }
        )


def test_start_model_rejects_ic_model() -> None:
    with pytest.raises(ValidationError, match="IC start model"):
        StartModelAction.model_validate(
            {
                "action": "start_model",
                "arguments": {
                    "outsystemsId": "2245",
                    "modelType": "inflatable curtain",
                },
            }
        )


def test_request_clarification_action_requires_both_fields() -> None:
    action = RequestClarificationAction.model_validate(
        {
            "action": "request_clarification",
            "arguments": {
                "question": "Which document should be closed?",
                "missingField": "documentName",
            },
        }
    )

    assert action.arguments.question == (
        "Which document should be closed?"
    )


def test_route_to_general_chat_action_takes_no_arguments() -> None:
    action = RouteToGeneralChatAction.model_validate(
        {
            "action": "route_to_general_chat",
            "arguments": {},
        }
    )

    assert action.action == "route_to_general_chat"


def test_clarification_required_result_shape() -> None:
    result = ClarificationRequiredResult(
        status="clarification_required",
        message="Which document should be closed?",
        missingField="documentName",
    )

    assert result.status == "clarification_required"
    assert result.missingField == "documentName"


def test_non_agentic_result_shape() -> None:
    result = NonAgenticResult(
        status="non_agentic",
        message="Route this to the general chat service.",
    )

    assert result.status == "non_agentic"


def test_unsupported_request_result_shape() -> None:
    result = UnsupportedRequestResult(
        status="unsupported_request",
        message="No available CATIA tool supports this request.",
    )

    assert result.status == "unsupported_request"


def test_agent_run_request_allows_missing_context() -> None:
    request = AgentRunRequest(
        sessionId="session-001",
        message="What is the active document?",
    )

    assert request.catiaContext is None


def test_agent_run_request_rejects_invalid_context() -> None:
    with pytest.raises(ValidationError):
        AgentRunRequest.model_validate(
            {
                "sessionId": "session-001",
                "message": "Open the gearbox.",
                "catiaContext": {
                    "isRunning": "not-a-boolean",
                },
            }
        )


def test_catia_context_defaults_empty_lists() -> None:
    context = CatiaContext(isRunning=False)

    assert context.openDocuments == []
    assert context.selectedObjects == []
    assert context.activeDocument is None


def test_action_execution_result_request_defaults() -> None:
    result = ActionExecutionResultRequest(
        actionId="action-001",
        success=True,
    )

    assert result.data is None
    assert result.errorCode is None
    assert result.errorMessage is None
