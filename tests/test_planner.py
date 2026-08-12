from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from catia_agent.models import (
    ActionRequiredResult,
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
from catia_agent.planner import (
    build_catia_context_message,
    create_typed_action,
    plan_catia_action,
    validate_close_cad_document,
    validate_open_cad_document,
)


# ---------------------------------------------------------
# create_typed_action
# ---------------------------------------------------------

def test_create_typed_action_maps_open_cad_document() -> None:
    action = create_typed_action(
        tool_name="open_cad_document",
        tool_arguments={
            "filePath": "C:\\TestData\\Gearbox.CATProduct",
        },
    )

    assert isinstance(action, OpenCadDocumentAction)


def test_create_typed_action_maps_get_active_document() -> None:
    action = create_typed_action(
        tool_name="get_active_document",
        tool_arguments={},
    )

    assert isinstance(action, GetActiveDocumentAction)


def test_create_typed_action_maps_close_cad_document() -> None:
    action = create_typed_action(
        tool_name="close_cad_document",
        tool_arguments={
            "targetMode": "active",
        },
    )

    assert isinstance(action, CloseCadDocumentAction)
    assert action.arguments.targetMode == "active"


def test_create_typed_action_maps_create_cad_document() -> None:
    action = create_typed_action(
        tool_name="create_cad_document",
        tool_arguments={
            "documentType": "CATPart",
        },
    )

    assert isinstance(action, CreateCadDocumentAction)
    assert action.arguments.documentType == "CATPart"


def test_create_typed_action_maps_download_cad() -> None:
    action = create_typed_action(
        tool_name="download_cad",
        tool_arguments={
            "partNumber": "6740898",
            "partRevision": "CAD002",
            "documentType": "drawing",
        },
    )

    assert isinstance(action, DownloadCadAction)
    assert action.arguments.documentType == "CATDrawing"


def test_create_typed_action_maps_start_model() -> None:
    action = create_typed_action(
        tool_name="start_model",
        tool_arguments={
            "partNumber": "6687874100A",
            "partRevision": "00",
            "modelType": "buckle",
        },
    )

    assert isinstance(action, StartModelAction)
    assert action.arguments.modelType == "SEATBELT"


def test_create_typed_action_maps_request_clarification() -> None:
    action = create_typed_action(
        tool_name="request_clarification",
        tool_arguments={
            "question": "Which document should be closed?",
            "missingField": "documentName",
        },
    )

    assert isinstance(action, RequestClarificationAction)


def test_create_typed_action_maps_route_to_general_chat() -> None:
    action = create_typed_action(
        tool_name="route_to_general_chat",
        tool_arguments={},
    )

    assert isinstance(action, RouteToGeneralChatAction)


def test_create_typed_action_rejects_unknown_tool() -> None:
    with pytest.raises(
        ValueError,
        match="unapproved tool",
    ):
        create_typed_action(
            tool_name="delete_all_documents",
            tool_arguments={},
        )


def test_create_typed_action_rejects_relative_open_path() -> None:
    with pytest.raises(ValueError, match="absolute Windows path"):
        create_typed_action(
            tool_name="open_cad_document",
            tool_arguments={
                "filePath": "TestData\\Gearbox.CATProduct",
            },
        )


def test_create_typed_action_rejects_unsupported_extension() -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported CATIA document extension",
    ):
        create_typed_action(
            tool_name="open_cad_document",
            tool_arguments={
                "filePath": "C:\\TestData\\Gearbox.pdf",
            },
        )


def test_create_typed_action_rejects_named_close_without_name() -> None:
    with pytest.raises(
        ValueError,
        match="documentName is required",
    ):
        create_typed_action(
            tool_name="close_cad_document",
            tool_arguments={
                "targetMode": "named",
            },
        )


def test_create_typed_action_rejects_invalid_download_revision() -> None:
    with pytest.raises(ValueError, match="CAD part numbers require"):
        create_typed_action(
            tool_name="download_cad",
            tool_arguments={
                "partNumber": "6740898",
                "partRevision": "00",
            },
        )


def test_create_typed_action_rejects_x_non_hyphen_revision() -> None:
    with pytest.raises(ValueError, match="hyphenated form"):
        create_typed_action(
            tool_name="download_cad",
            tool_arguments={
                "partNumber": "X670830400C",
                "partRevision": "00",
            },
        )


# ---------------------------------------------------------
# validate_open_cad_document
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "file_path",
    [
        "C:\\TestData\\Gearbox.CATProduct",
        "C:\\TestData\\Bracket.CATPart",
        "C:\\TestData\\Drawing.CATDrawing",
    ],
)
def test_validate_open_cad_document_accepts_supported_extensions(
    file_path: str,
) -> None:
    action = OpenCadDocumentAction.model_validate(
        {
            "action": "open_cad_document",
            "arguments": {"filePath": file_path},
        }
    )

    validate_open_cad_document(action)


# ---------------------------------------------------------
# validate_close_cad_document
# ---------------------------------------------------------

def test_validate_close_cad_document_trims_document_name() -> None:
    action = CloseCadDocumentAction.model_validate(
        {
            "action": "close_cad_document",
            "arguments": {
                "targetMode": "named",
                "documentName": "  Gearbox.CATProduct  ",
            },
        }
    )

    validate_close_cad_document(action)

    assert action.arguments.documentName == "Gearbox.CATProduct"


def test_validate_close_cad_document_clears_name_when_not_named() -> None:
    action = CloseCadDocumentAction.model_validate(
        {
            "action": "close_cad_document",
            "arguments": {
                "targetMode": "active",
                "documentName": "Gearbox.CATProduct",
            },
        }
    )

    validate_close_cad_document(action)

    assert action.arguments.documentName is None


def test_validate_close_cad_document_rejects_blank_name() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        CloseCadDocumentAction.model_validate(
            {
                "action": "close_cad_document",
                "arguments": {
                    "targetMode": "named",
                    "documentName": "   ",
                },
            }
        )


# ---------------------------------------------------------
# build_catia_context_message
# ---------------------------------------------------------

def test_build_catia_context_message_handles_missing_context() -> None:
    message = build_catia_context_message(None)

    assert "not currently running" not in message
    assert "not supplied" in message


def test_build_catia_context_message_handles_catia_not_running() -> None:
    from catia_agent.models import CatiaContext

    message = build_catia_context_message(
        CatiaContext(isRunning=False)
    )

    assert "CATIA is not currently running" in message


def test_build_catia_context_message_lists_open_documents() -> None:
    from catia_agent.models import CatiaContext, CatiaDocumentContext

    context = CatiaContext(
        isRunning=True,
        activeDocument=CatiaDocumentContext(name="Gearbox.CATProduct"),
        openDocuments=[
            CatiaDocumentContext(name="Gearbox.CATProduct"),
            CatiaDocumentContext(name="Frame.CATProduct"),
        ],
    )

    message = build_catia_context_message(context)

    assert "Gearbox.CATProduct" in message
    assert "Frame.CATProduct" in message


# ---------------------------------------------------------
# plan_catia_action orchestration (mocked Ollama + MCP)
# ---------------------------------------------------------

def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "function": {
            "name": name,
            "arguments": arguments,
        }
    }


@pytest.fixture(autouse=True)
def _mock_tool_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_load_tools(force_refresh: bool = False):
        return [
            {
                "type": "function",
                "function": {
                    "name": "open_cad_document",
                    "description": "Open a CATIA document.",
                    "parameters": {},
                },
            }
        ]

    monkeypatch.setattr(
        "catia_agent.planner.load_ollama_tools_from_mcp",
        fake_load_tools,
    )


@pytest.mark.asyncio
async def test_plan_catia_action_returns_action_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call(
                    "open_cad_document",
                    {"filePath": "C:\\TestData\\Gearbox.CATProduct"},
                )
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Open the gearbox.",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.action == "open_cad_document"


@pytest.mark.asyncio
async def test_plan_catia_action_parses_json_string_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Azure AI Foundry's vLLM deployment returns arguments as a
    # JSON-encoded string (plus extra fields like "reasoning" and
    # "refusal"), not a JSON object, unlike Ollama.
    fake_response = {
        "message": {
            "content": "",
            "refusal": None,
            "tool_calls": [
                {
                    "id": "chatcmpl-tool-9761220fc49a4cf1",
                    "type": "function",
                    "function": {
                        "name": "request_clarification",
                        "arguments": (
                            '{"question": "What type of document?", '
                            '"missingField": "documentType"}'
                        ),
                    },
                }
            ],
            "reasoning": "The user is asking two things...",
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Can you please open a new document?",
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "documentType"


@pytest.mark.asyncio
async def test_plan_catia_action_returns_non_agentic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call("route_to_general_chat", {})
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="What is the capital of France?",
    )

    assert isinstance(result, NonAgenticResult)


@pytest.mark.asyncio
async def test_plan_catia_action_returns_clarification_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call(
                    "request_clarification",
                    {
                        "question": "Which document should be closed?",
                        "missingField": "documentName",
                    },
                )
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Close a document.",
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "documentName"


@pytest.mark.asyncio
async def test_plan_catia_action_returns_unsupported_when_no_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Move the component 5mm.",
    )

    assert result.status == "planning_error"
    assert result.errorCode == "MODEL_RETURNED_NO_DECISION"


@pytest.mark.asyncio
async def test_plan_catia_action_prefers_catia_tool_over_meta_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call("route_to_general_chat", {}),
                _tool_call(
                    "open_cad_document",
                    {"filePath": "C:\\TestData\\Gearbox.CATProduct"},
                ),
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Open the gearbox.",
    )

    assert result.status == "planning_error"
    assert result.errorCode == "MODEL_RETURNED_MULTIPLE_DECISIONS"


@pytest.mark.asyncio
async def test_plan_catia_action_picks_first_when_only_meta_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call("route_to_general_chat", {}),
                _tool_call("route_to_general_chat", {}),
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Hello.",
    )

    assert result.status == "planning_error"
    assert result.errorCode == "MODEL_RETURNED_MULTIPLE_DECISIONS"


@pytest.mark.asyncio
async def test_plan_catia_action_trims_conversation_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_chat(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        fake_chat,
    )

    monkeypatch.setattr(
        "catia_agent.config.settings.max_conversation_history_messages",
        2,
    )

    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "second reply"},
    ]

    await plan_catia_action(
        user_command="third",
        conversation_history=history,
    )

    sent_messages = captured["messages"]

    # Last 2 history entries plus the two system messages and the
    # final user turn should be present, and older history dropped.
    contents = [m.get("content") for m in sent_messages]

    assert "first" not in contents
    assert "second" in contents
    assert "third" in contents


@pytest.mark.asyncio
async def test_plan_catia_action_injects_completed_actions_system_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_chat(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        fake_chat,
    )

    completed = [
        {
            "action": "open_cad_document",
            "arguments": {
                "filePath": "C:\\TestData\\Gearbox.CATProduct",
            },
        }
    ]

    await plan_catia_action(
        user_command="Open the gearbox.",
        completed_actions=completed,
    )

    system_contents = [
        m["content"]
        for m in captured["messages"]
        if m["role"] == "system"
    ]

    joined = "\n".join(system_contents)

    assert "already completed" in joined
    assert "open_cad_document" in joined
    assert "Gearbox.CATProduct" in joined


@pytest.mark.asyncio
async def test_plan_catia_action_no_completed_actions_message_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_chat(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        fake_chat,
    )

    await plan_catia_action(
        user_command="Open the gearbox.",
        completed_actions=None,
    )

    system_contents = [
        m["content"]
        for m in captured["messages"]
        if m["role"] == "system"
    ]

    joined = "\n".join(system_contents)

    assert "Goal-delta analysis for this planning step." not in joined


@pytest.mark.asyncio
async def test_plan_catia_action_no_decision_with_completed_actions_defaults_to_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Open the gearbox, then close it.",
        completed_actions=[
            {
                "action": "open_cad_document",
                "arguments": {
                    "filePath": "C:\\TestData\\Gearbox.CATProduct",
                },
            }
        ],
    )

    assert result.status == "request_completed"


@pytest.mark.asyncio
async def test_plan_catia_action_no_decision_blocks_complete_for_high_confidence_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Open the gearbox, then close it.",
        completed_actions=[
            {
                "action": "open_cad_document",
                "arguments": {
                    "filePath": "C:\\TestData\\Gearbox.CATProduct",
                },
            }
        ],
        planning_mode="follow_up_required",
        planning_mode_confidence="high",
    )

    assert result.status == "planning_error"
    assert result.errorCode == "MODEL_RETURNED_NO_DECISION"


@pytest.mark.asyncio
async def test_plan_catia_action_rejects_ic_start_model_request_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_mock = AsyncMock(
        return_value={"message": {"tool_calls": []}}
    )

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        chat_mock,
    )

    result = await plan_catia_action(
        user_command=(
            "Start IC model for outsystems ID 2245."
        ),
    )

    assert isinstance(result, UnsupportedRequestResult)
    assert result.reason == "operation_not_allowed"
    assert "IC start model" in result.message
    chat_mock.assert_not_called()


@pytest.mark.asyncio
async def test_plan_catia_action_logs_tool_names_on_multiple_decisions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call("route_to_general_chat", {}),
                _tool_call(
                    "open_cad_document",
                    {"filePath": "C:\\TestData\\Gearbox.CATProduct"},
                ),
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=fake_response),
    )

    with caplog.at_level("ERROR"):
        result = await plan_catia_action(
            user_command="Open the gearbox.",
        )

    assert result.status == "planning_error"

    multi_decision_records = [
        record
        for record in caplog.records
        if record.message == "model_returned_multiple_decisions"
    ]

    assert len(multi_decision_records) == 1
    assert multi_decision_records[0].selectedTools == [
        "route_to_general_chat",
        "open_cad_document",
    ]

