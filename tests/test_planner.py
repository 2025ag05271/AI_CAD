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
        _guard_master_drawing_arguments,
    build_catia_context_message,
    create_typed_action,
    plan_catia_action,
    validate_close_cad_document,
    validate_open_cad_document,
)
from catia_agent.tool_catalog import get_model_tools


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
# plan_catia_action orchestration (mocked model + tool catalog)
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_production_tool_catalog_contains_only_master_drawing() -> None:
    tools = await get_model_tools()

    assert [tool["function"]["name"] for tool in tools] == [
        "open_master_drawing"
    ]

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
        "catia_agent.planner.get_model_tools",
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
async def test_plan_catia_action_sends_tools_in_deterministic_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deliberately out-of-alphabetical-order and interleaved with the
    # orchestrator tools' names, to prove the final list is sorted
    # rather than just concatenated in catalog/declaration order. This
    # keeps the tools portion of the prompt byte-identical across
    # calls for Azure Foundry/vLLM prefix-cache reuse (see planner.py).
    async def fake_load_tools(force_refresh: bool = False):
        return [
            {
                "type": "function",
                "function": {
                    "name": "start_model",
                    "description": "Start the SAB model.",
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "close_cad_document",
                    "description": "Close a CATIA document.",
                    "parameters": {},
                },
            },
        ]

    monkeypatch.setattr(
        "catia_agent.planner.get_model_tools",
        fake_load_tools,
    )

    captured: dict = {}

    async def fake_chat(**kwargs):
        captured["tools"] = kwargs["tools"]
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        fake_chat,
    )

    await plan_catia_action(
        user_command="Open the gearbox.",
    )

    tool_names = [
        tool["function"]["name"]
        for tool in captured["tools"]
    ]

    assert tool_names == sorted(tool_names)


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


@pytest.mark.asyncio
async def test_plan_catia_action_uses_initial_prompt_on_first_turn(
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

    assert "Step 1: decide whether" in joined
    assert "continuing to plan CATIA actions" not in joined


@pytest.mark.asyncio
async def test_plan_catia_action_uses_leaner_prompt_on_followup_turn(
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
        user_command="Open the gearbox, then close it.",
        completed_actions=completed,
    )

    system_contents = [
        m["content"]
        for m in captured["messages"]
        if m["role"] == "system"
    ]

    joined = "\n".join(system_contents)

    assert "continuing to plan CATIA actions" in joined
    assert "Step 1: decide whether" not in joined
    # Orchestrator-tool guidance for mid-sequence terminal outcomes
    # (README Section 8.2) must still be present in the leaner prompt.
    assert "request_clarification" in joined
    assert "report_unsupported_catia_action" in joined
    assert "route_to_general_chat" in joined
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


# ---------------------------------------------------------
# open_master_drawing
# ---------------------------------------------------------

def _mock_master_drawing_call(
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict,
) -> None:
    async def fake_load_tools(force_refresh: bool = False):
        return [
            {
                "type": "function",
                "function": {
                    "name": "open_master_drawing",
                    "description": "Open a master drawing.",
                    "parameters": {},
                },
            }
        ]

    monkeypatch.setattr(
        "catia_agent.planner.get_model_tools",
        fake_load_tools,
    )
    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(
            return_value={
                "message": {
                    "tool_calls": [
                        _tool_call("open_master_drawing", arguments)
                    ]
                }
            }
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_command", "arguments", "expected_revision"),
    [
        (
            "Open the master drawing for 674089800A",
            {"partNumber": "674089800A"},
            None,
        ),
        (
            "open master drawing of part 674089800a revision 01",
            {"partNumber": "674089800a", "partRevision": "01"},
            "01",
        ),
    ],
)
async def test_open_master_drawing_action_required(
    monkeypatch: pytest.MonkeyPatch,
    user_command: str,
    arguments: dict,
    expected_revision: str | None,
) -> None:
    _mock_master_drawing_call(monkeypatch, arguments)

    result = await plan_catia_action(user_command=user_command)

    assert isinstance(result, ActionRequiredResult)
    assert result.action.action == "open_master_drawing"
    assert result.action.arguments.partNumber == "674089800A"
    assert result.action.arguments.partRevision == expected_revision


@pytest.mark.asyncio
async def test_open_master_drawing_with_only_revision_asks_for_part_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(monkeypatch, {"partRevision": "01"})

    result = await plan_catia_action(
        user_command="Open the master drawing revision 01",
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partNumber"


@pytest.mark.asyncio
async def test_open_master_drawing_invented_part_number_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partNumber": "123456789Z", "partRevision": "01"},
    )

    result = await plan_catia_action(
        user_command="Open the master drawing revision 01",
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partNumber"


@pytest.mark.asyncio
async def test_open_master_drawing_part_number_from_earlier_turn_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partNumber": "674089800A", "partRevision": "01"},
    )

    result = await plan_catia_action(
        user_command="Revision 01",
        conversation_history=[
            {"role": "user", "content": "Open the master drawing"},
            {
                "role": "assistant",
                "content": "Which part number? 674089800A?",
            },
            {"role": "user", "content": "It is 674089800A"},
        ],
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.arguments.partRevision == "01"


@pytest.mark.asyncio
async def test_open_master_drawing_drops_invented_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partNumber": "674089800A", "partRevision": "07"},
    )

    result = await plan_catia_action(
        user_command="Open the master drawing for 674089800A",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.arguments.partRevision is None


@pytest.mark.asyncio
async def test_part_number_clarification_resolves_title_from_user_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "message": {
            "tool_calls": [
                _tool_call(
                    "request_clarification",
                    {
                        "question": (
                            "What is the part number or part title for "
                            "the backshell drawing you want to open?"
                        ),
                        "missingField": "partNumber",
                    },
                )
            ]
        }
    }
    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=response),
    )

    result = await plan_catia_action(
        user_command="Can you please open backshell?",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.action == "open_master_drawing"
    assert result.action.arguments.partNumber == "6481212"
    assert result.action.arguments.masterDrawingOrOgdUrl.endswith(
        "/Drawing%20Print/6481212/*"
    )


@pytest.mark.asyncio
async def test_part_number_clarification_returns_catalog_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "message": {
            "tool_calls": [
                _tool_call(
                    "request_clarification",
                    {
                        "question": (
                            "What part number is the spring dab retention "
                            "drawing for?"
                        ),
                        "missingField": "partNumber",
                    },
                )
            ]
        }
    }
    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=response),
    )

    result = await plan_catia_action(
        user_command=(
            "Open the master drawing for spring dab retention in SW ASW8."
        ),
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partTitle"
    assert len(result.options or []) == 3
    assert {option.partNumber for option in result.options or []} == {
        "6446798",
        "6471900",
        "6557983",
    }


@pytest.mark.asyncio
async def test_title_clarification_does_not_ignore_explicit_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "message": {
            "tool_calls": [
                _tool_call(
                    "request_clarification",
                    {
                        "question": "Which part number?",
                        "missingField": "partNumber",
                    },
                )
            ]
        }
    }
    monkeypatch.setattr(
        "catia_agent.planner.model_client.select_tool",
        AsyncMock(return_value=response),
    )

    result = await plan_catia_action(
        user_command=(
            "Open the master drawing of backshell revision 01."
        ),
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.message == "Which part number?"


def test_master_drawing_title_guard_injects_catalog_link() -> None:
    arguments = {"partTitle": "backshell"}

    result = _guard_master_drawing_arguments(
        arguments,
        ["Can you open the master drawing of backshell?"],
    )

    assert result is None
    action = create_typed_action(
        "open_master_drawing",
        arguments,
    )
    assert action.arguments.partNumber == "6481212"
    assert action.arguments.masterDrawingOrOgdUrl.endswith(
        "/Drawing%20Print/6481212/*"
    )


def test_master_drawing_title_guard_asks_when_catalog_match_is_ambiguous() -> None:
    result = _guard_master_drawing_arguments(
        {"partTitle": "spring dab retention in sw asw8"},
        ["Open the master drawing for spring dab retention in SW ASW8."],
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partTitle"
    assert "multiple master-drawing matches" in result.message


@pytest.mark.asyncio
async def test_open_master_drawing_title_resolves_catalog_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partTitle": "backshell"},
    )

    result = await plan_catia_action(
        user_command="Can you open the master drawing of backshell?",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.arguments.partNumber == "6481212"
    assert result.action.arguments.partTitle == "backshell"
    assert result.action.arguments.masterDrawingOrOgdLabel == "6481212"
    assert result.action.arguments.masterDrawingOrOgdUrl.endswith(
        "/Drawing%20Print/6481212/*"
    )


@pytest.mark.asyncio
async def test_open_master_drawing_fabric_roll_resolves_catalog_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partTitle": "fabric roll"},
    )

    result = await plan_catia_action(
        user_command="Open fabric roll drawing.",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.arguments.partNumber == "6536096"
    assert result.action.arguments.masterDrawingOrOgdUrl.endswith(
        "/PartDrawing/6536096/%2a"
    )


@pytest.mark.asyncio
async def test_open_master_drawing_recovers_title_from_explicit_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(monkeypatch, {})

    result = await plan_catia_action(
        user_command=(
            "Can you please open the deco part garnish drawing?"
        ),
        planning_mode="single_action_fast_path",
        planning_mode_confidence="high",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.arguments.partNumber == "6481213"
    assert result.action.arguments.plmStandardTitle == (
        "DECO PART GARNISH, XXX"
    )


@pytest.mark.asyncio
async def test_open_master_drawing_text_fallback_preserves_revision_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(monkeypatch, {})

    result = await plan_catia_action(
        user_command=(
            "Open the master drawing for deco part garnish revision 02."
        ),
        planning_mode="single_action_fast_path",
        planning_mode_confidence="high",
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partNumber"


@pytest.mark.asyncio
async def test_open_master_drawing_ambiguous_title_asks_for_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_master_drawing_call(
        monkeypatch,
        {"partTitle": "spring dab retention in sw asw8"},
    )

    result = await plan_catia_action(
        user_command=(
            "Open the master drawing for spring dab retention in SW ASW8."
        ),
    )

    assert isinstance(result, ClarificationRequiredResult)
    assert result.missingField == "partTitle"
    assert "multiple master-drawing matches" in result.message

