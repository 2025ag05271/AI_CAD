from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from catia_agent.models import (
    ActionRequiredResult,
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
        match="no approved typed model",
    ):
        create_typed_action(
            tool_name="delete_all_documents",
            tool_arguments={},
        )


def test_create_typed_action_rejects_relative_open_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
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
    action = CloseCadDocumentAction.model_validate(
        {
            "action": "close_cad_document",
            "arguments": {
                "targetMode": "named",
                "documentName": "   ",
            },
        }
    )

    with pytest.raises(ValueError, match="documentName is required"):
        validate_close_cad_document(action)


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
        "catia_agent.planner.ollama_client.chat",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Open the gearbox.",
    )

    assert isinstance(result, ActionRequiredResult)
    assert result.action.action == "open_cad_document"


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
        "catia_agent.planner.ollama_client.chat",
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
        "catia_agent.planner.ollama_client.chat",
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
        "catia_agent.planner.ollama_client.chat",
        AsyncMock(return_value=fake_response),
    )

    result = await plan_catia_action(
        user_command="Move the component 5mm.",
    )

    assert isinstance(result, UnsupportedRequestResult)


@pytest.mark.asyncio
async def test_plan_catia_action_rejects_multiple_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = {
        "message": {
            "tool_calls": [
                _tool_call("open_cad_document", {"filePath": "x"}),
                _tool_call("get_active_document", {}),
            ]
        }
    }

    monkeypatch.setattr(
        "catia_agent.planner.ollama_client.chat",
        AsyncMock(return_value=fake_response),
    )

    with pytest.raises(ValueError, match="exactly one CATIA tool call"):
        await plan_catia_action(user_command="Do two things.")


@pytest.mark.asyncio
async def test_plan_catia_action_trims_conversation_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_chat(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(
        "catia_agent.planner.ollama_client.chat",
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
