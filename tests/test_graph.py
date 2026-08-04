from uuid import uuid4

import pytest

from catia_agent.graph import resume_catia_agent, start_catia_agent
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CreateCadDocumentAction,
    NonAgenticResult,
    OpenCadDocumentAction,
)


def _patch_planner(monkeypatch: pytest.MonkeyPatch, result):
    async def fake_plan_catia_action(
        user_command,
        catia_context=None,
        conversation_history=None,
    ):
        fake_plan_catia_action.last_history = conversation_history
        return result

    monkeypatch.setattr(
        "catia_agent.graph.plan_catia_action",
        fake_plan_catia_action,
    )

    return fake_plan_catia_action


def _open_action_result() -> ActionRequiredResult:
    return ActionRequiredResult(
        status="action_required",
        action=OpenCadDocumentAction.model_validate(
            {
                "action": "open_cad_document",
                "arguments": {
                    "filePath": "C:\\TestData\\Gearbox.CATProduct",
                },
            }
        ),
    )


# ---------------------------------------------------------
# Terminal (non-action) results
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_start_catia_agent_returns_non_agentic_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        NonAgenticResult(
            status="non_agentic",
            message="Route this to the general chat service.",
        ),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="What is the capital of France?",
    )

    assert response["status"] == "non_agentic"
    assert "message" in response


@pytest.mark.asyncio
async def test_start_catia_agent_returns_clarification_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        ClarificationRequiredResult(
            status="clarification_required",
            message="Which document should be closed?",
            missingField="documentName",
        ),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Close a document.",
    )

    assert response["status"] == "clarification_required"
    assert response["missingField"] == "documentName"


@pytest.mark.asyncio
async def test_start_catia_agent_rejects_empty_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, None)

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="   ",
    )

    assert response["status"] == "invalid_request"


# ---------------------------------------------------------
# Action lifecycle: pause then resume
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_action_required_pauses_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox.",
    )

    assert response["status"] == "action_required"
    assert response["action"]["action"] == "open_cad_document"
    assert "actionId" in response
    assert "runId" in response


@pytest.mark.asyncio
async def test_resume_completes_open_cad_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox.",
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert result["status"] == "completed"
    assert "Gearbox.CATProduct" in result["message"]


@pytest.mark.asyncio
async def test_resume_reports_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox.",
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=False,
            errorCode="FILE_NOT_FOUND",
            errorMessage="The file could not be found.",
        ),
    )

    assert result["status"] == "execution_failed"
    assert result["errorCode"] == "FILE_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_close_cad_document_reports_closed_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_action = ActionRequiredResult(
        status="action_required",
        action=CloseCadDocumentAction.model_validate(
            {
                "action": "close_cad_document",
                "arguments": {"targetMode": "all"},
            }
        ),
    )

    _patch_planner(monkeypatch, close_action)

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Close all documents.",
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={
                "closedDocuments": [
                    "Gearbox.CATProduct",
                    "Frame.CATProduct",
                ]
            },
        ),
    )

    assert result["status"] == "completed"
    assert "2 CATIA documents" in result["message"]


@pytest.mark.asyncio
async def test_resume_create_cad_document_reports_new_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_action = ActionRequiredResult(
        status="action_required",
        action=CreateCadDocumentAction.model_validate(
            {
                "action": "create_cad_document",
                "arguments": {"documentType": "CATPart"},
            }
        ),
    )

    _patch_planner(monkeypatch, create_action)

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Create a new part.",
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={"documentName": "Part1.CATPart"},
        ),
    )

    assert result["status"] == "completed"
    assert "Part1.CATPart" in result["message"]


# ---------------------------------------------------------
# Guard rails
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_starting_new_run_while_action_pending_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    session_id = str(uuid4())

    await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    with pytest.raises(ValueError, match="pending CATIA action"):
        await start_catia_agent(
            session_id=session_id,
            user_message="Open another document.",
        )


@pytest.mark.asyncio
async def test_resume_unknown_run_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        await resume_catia_agent(
            run_id=str(uuid4()),
            action_result=ActionExecutionResultRequest(
                actionId=str(uuid4()),
                success=True,
            ),
        )


@pytest.mark.asyncio
async def test_resume_rejects_mismatched_action_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox.",
    )

    with pytest.raises(ValueError, match="does not match"):
        await resume_catia_agent(
            run_id=pending["runId"],
            action_result=ActionExecutionResultRequest(
                actionId=str(uuid4()),
                success=True,
            ),
        )


# ---------------------------------------------------------
# Conversation history across turns
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_conversation_history_persists_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_planner = _patch_planner(
        monkeypatch,
        NonAgenticResult(
            status="non_agentic",
            message="Route this to the general chat service.",
        ),
    )

    session_id = str(uuid4())

    await start_catia_agent(
        session_id=session_id,
        user_message="Hello there.",
    )

    assert fake_planner.last_history == []

    await start_catia_agent(
        session_id=session_id,
        user_message="Second message.",
    )

    history = fake_planner.last_history

    assert {"role": "user", "content": "Hello there."} in history
    assert any(
        turn["role"] == "assistant"
        for turn in history
    )
