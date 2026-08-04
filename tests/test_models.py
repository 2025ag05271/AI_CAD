import pytest
from pydantic import ValidationError

from catia_agent.models import (
    ActionExecutionResultRequest,
    AgentRunRequest,
    CatiaContext,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CreateCadDocumentAction,
    GetActiveDocumentAction,
    NonAgenticResult,
    OpenCadDocumentAction,
    RequestClarificationAction,
    RouteToGeneralChatAction,
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
