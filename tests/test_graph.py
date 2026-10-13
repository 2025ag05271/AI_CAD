import asyncio
from uuid import uuid4

import pytest

import catia_agent.graph as graph_module
from catia_agent.graph import (
    AgentRunNotFoundError,
    AgentRunStateConflictError,
    _classify_planning_mode,
    get_session_id_for_run,
    resume_catia_agent,
    start_catia_agent,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CreateCadDocumentAction,
    InvalidRequestResult,
    NonAgenticResult,
    OpenCadDocumentAction,
    PlanningErrorResult,
    RequestCompletedResult,
    StartModelAction,
)


_SEQUENCE_COMPLETE = RequestCompletedResult(
    status="request_completed",
)


@pytest.fixture(autouse=True)
def _enable_sequences_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both default to False (see config.py); most of this file predates
    # those toggles and exercises multi-action chaining and cross-turn
    # history, so restore that behavior here. Tests for the disabled
    # defaults override these back to False explicitly.
    monkeypatch.setattr(
        "catia_agent.graph.settings.enable_multi_action_sequences",
        True,
    )
    monkeypatch.setattr(
        "catia_agent.graph.settings.enable_conversation_history",
        True,
    )


@pytest.fixture(autouse=True)
def _stub_embedding_intent_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This file predates the embedding intent classifier and exercises
    # planner behavior through the mocked plan_catia_action; a real
    # classification here (a local Ollama call) would both make tests
    # depend on a running Ollama instance and could short-circuit
    # plan_action_node before the mocked planner ever runs. Default to
    # "agentic" (never short-circuit) so every test always reaches the
    # planner mock as before. Tests for the classifier itself override
    # this back explicitly.
    async def fake_is_agentic_request(user_message: str) -> bool:
        return True

    monkeypatch.setattr(
        "catia_agent.graph.is_agentic_request",
        fake_is_agentic_request,
    )


def _patch_planner(monkeypatch: pytest.MonkeyPatch, result):
    """
    Patch the planner with either one fixed result, or a list of
    results consumed one per call (the last one repeats if the
    planner is called more times than the list has entries).
    """

    results = result if isinstance(result, list) else [result]

    async def fake_plan_catia_action(
        user_command,
        catia_context=None,
        conversation_history=None,
        completed_actions=None,
        retry_hint=None,
        planning_mode=None,
        planning_mode_confidence=None,
    ):
        fake_plan_catia_action.last_history = conversation_history
        fake_plan_catia_action.last_completed_actions = completed_actions
        fake_plan_catia_action.last_retry_hint = retry_hint
        index = min(
            fake_plan_catia_action.call_count,
            len(results) - 1,
        )
        fake_plan_catia_action.call_count += 1
        return results[index]

    fake_plan_catia_action.call_count = 0

    monkeypatch.setattr(
        "catia_agent.graph_nodes.plan_catia_action",
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


def test_classify_planning_mode_mixed_verbs_single_catia_intent_fast_path() -> (
    None
):
    mode, confidence, signals, expected_count = (
        _classify_planning_mode(
            "I need to load bom details of a part and create seat "
            "belt assembly."
        )
    )

    assert mode == "single_action_fast_path"
    assert confidence == "medium"
    assert "mixed_predicates_single_catia_intent" in signals
    assert expected_count == 1


def test_classify_planning_mode_true_multi_action_stays_follow_up() -> None:
    mode, confidence, _, expected_count = (
        _classify_planning_mode(
            "Open the document and close it."
        )
    )

    assert mode == "follow_up_required"
    assert confidence == "high"
    assert expected_count == 2


def test_classify_planning_mode_single_command_is_high_confidence() -> None:
    mode, confidence, _, expected_count = (
        _classify_planning_mode(
            "cool, can you please close all the open documents "
            "which are open?"
        )
    )

    assert mode == "single_action_fast_path"
    assert confidence == "high"
    assert expected_count == 1


def test_classify_planning_mode_no_catia_verb_is_low_confidence() -> None:
    mode, confidence, signals, expected_count = (
        _classify_planning_mode("Hello there.")
    )

    assert mode == "follow_up_required"
    assert confidence == "low"
    assert "no_catia_verbs" in signals
    assert expected_count is None


def test_classify_planning_mode_empty_message() -> None:
    mode, confidence, signals, expected_count = (
        _classify_planning_mode("   ")
    )

    assert mode == "follow_up_required"
    assert confidence == "low"
    assert "empty_message" in signals
    assert expected_count is None


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
async def test_embedding_classifier_routes_to_general_chat_without_calling_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_is_agentic_request(user_message: str) -> bool:
        return False

    monkeypatch.setattr(
        "catia_agent.graph.is_agentic_request",
        fake_is_agentic_request,
    )

    fake_plan_catia_action = _patch_planner(
        monkeypatch,
        _open_action_result(),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="What is the capital of France?",
    )

    assert response["status"] == "non_agentic"
    assert fake_plan_catia_action.call_count == 0


@pytest.mark.asyncio
async def test_embedding_classifier_uncertain_falls_back_to_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_is_agentic_request(user_message: str) -> None:
        return None

    monkeypatch.setattr(
        "catia_agent.graph.is_agentic_request",
        fake_is_agentic_request,
    )

    fake_plan_catia_action = _patch_planner(
        monkeypatch,
        _open_action_result(),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox.",
    )

    assert response["status"] == "action_required"
    assert fake_plan_catia_action.call_count == 1


@pytest.mark.asyncio
async def test_explicit_master_drawing_command_overrides_embedding_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_is_agentic_request(user_message: str) -> bool:
        return False

    monkeypatch.setattr(
        "catia_agent.graph.is_agentic_request",
        fake_is_agentic_request,
    )

    fake_plan_catia_action = _patch_planner(
        monkeypatch,
        _open_action_result(),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message=(
            "Can you please open the master drawing of 674898900A"
        ),
    )

    assert response["status"] == "action_required"
    assert fake_plan_catia_action.call_count == 1


@pytest.mark.asyncio
async def test_informational_open_question_still_routes_to_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_is_agentic_request(user_message: str) -> bool:
        return False

    monkeypatch.setattr(
        "catia_agent.graph.is_agentic_request",
        fake_is_agentic_request,
    )

    fake_plan_catia_action = _patch_planner(
        monkeypatch,
        _open_action_result(),
    )

    response = await start_catia_agent(
        session_id=str(uuid4()),
        user_message=(
            "How do I open a master drawing in CATIA?"
        ),
    )

    assert response["status"] == "non_agentic"
    assert fake_plan_catia_action.call_count == 0


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
    _patch_planner(
        monkeypatch,
        InvalidRequestResult(
            status="invalid_request",
            message="The user message must not be empty.",
        ),
    )

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
    fake_planner = _patch_planner(
        monkeypatch,
        [_open_action_result(), _SEQUENCE_COMPLETE],
    )

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
    assert fake_planner.call_count == 1


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

    _patch_planner(
        monkeypatch,
        [close_action, _SEQUENCE_COMPLETE],
    )

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
    assert "Gearbox.CATProduct" in result["message"]
    assert "Frame.CATProduct" in result["message"]


@pytest.mark.asyncio
async def test_close_all_open_documents_uses_single_action_fast_path(
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

    fake_planner = _patch_planner(
        monkeypatch,
        [close_action, _SEQUENCE_COMPLETE],
    )

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message=(
            "cool, can you please close all the open documents "
            "which are open?"
        ),
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={
                "closedDocuments": [
                    "6328629.CATPart",
                    "Product1.CATProduct",
                ]
            },
        ),
    )

    assert result["status"] == "completed"
    assert fake_planner.call_count == 1


@pytest.mark.asyncio
async def test_mixed_predicates_single_intent_medium_confidence_skips_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_model_action = _start_model_action_result()

    fake_planner = _patch_planner(
        monkeypatch,
        [start_model_action, _SEQUENCE_COMPLETE],
    )

    # Force the initial planning-mode classification to match
    # mixed predicates with one CATIA intent.
    monkeypatch.setattr(
        "catia_agent.graph._classify_planning_mode",
        lambda _message: (
            "single_action_fast_path",
            "medium",
            [
                "expected_catia_actions:1",
                "mixed_predicates_single_catia_intent",
            ],
            1,
        ),
    )

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message=(
            "Please load seatbelt assembly for this part and revision."
        ),
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={
                "success_message": (
                    "SEATBELT model started successfully."
                )
            },
        ),
    )

    assert result["status"] == "completed"
    assert len(result["completedActions"]) == 1
    assert fake_planner.call_count == 1


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

    _patch_planner(
        monkeypatch,
        [create_action, _SEQUENCE_COMPLETE],
    )

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


def _start_model_action_result() -> ActionRequiredResult:
    return ActionRequiredResult(
        status="action_required",
        action=StartModelAction.model_validate(
            {
                "action": "start_model",
                "arguments": {
                    "outsystemsId": "2245",
                    "afisNumber": "AFIS-1001",
                    "modelType": "SAB",
                },
            }
        ),
    )


@pytest.mark.asyncio
async def test_resume_start_model_uses_success_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [_start_model_action_result(), _SEQUENCE_COMPLETE],
    )

    pending = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Start the SAB model for AO ID 2245.",
    )

    result = await resume_catia_agent(
        run_id=pending["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending["actionId"],
            success=True,
            data={
                "success_message": (
                    "SAB model started successfully."
                )
            },
        ),
    )

    assert result["status"] == "completed"
    assert result["message"] == "SAB model started successfully."


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

    with pytest.raises(
        ValueError,
        match="waiting for a CATIA action result",
    ):
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


@pytest.mark.asyncio
async def test_conversation_history_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.graph.settings.enable_conversation_history",
        False,
    )

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

    await start_catia_agent(
        session_id=session_id,
        user_message="Second message.",
    )

    assert fake_planner.last_history == []


# ---------------------------------------------------------
# Multi-action sequences within a single run
# ---------------------------------------------------------

def _close_action_result(target_mode: str = "all") -> ActionRequiredResult:
    return ActionRequiredResult(
        status="action_required",
        action=CloseCadDocumentAction.model_validate(
            {
                "action": "close_cad_document",
                "arguments": {"targetMode": target_mode},
            }
        ),
    )


@pytest.mark.asyncio
async def test_sequence_chains_open_then_close_into_one_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_planner = _patch_planner(
        monkeypatch,
        [_open_action_result(), _close_action_result()],
    )

    session_id = str(uuid4())

    pending_open = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox, then close it.",
    )

    assert pending_open["action"]["action"] == "open_cad_document"

    pending_close = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert pending_close["status"] == "action_required"
    assert pending_close["action"]["action"] == "close_cad_document"
    assert pending_close["runId"] == pending_open["runId"]

    result = await resume_catia_agent(
        run_id=pending_close["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_close["actionId"],
            success=True,
            data={"closedDocuments": ["Gearbox.CATProduct"]},
        ),
    )

    assert result["status"] == "completed"
    assert "Gearbox.CATProduct was opened successfully." in result["message"]
    assert "Gearbox.CATProduct closed successfully." in result["message"]
    assert len(result["completedActions"]) == 2
    assert fake_planner.call_count == 2


@pytest.mark.asyncio
async def test_multi_action_sequences_disabled_by_default_stops_after_first_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.graph.settings.enable_multi_action_sequences",
        False,
    )

    fake_planner = _patch_planner(
        monkeypatch,
        [_open_action_result(), _close_action_result()],
    )

    session_id = str(uuid4())

    pending_open = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox, then close it.",
    )

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    # Finalizes as completed after the single action instead of looping
    # back to the planner for the "close it" half of the request.
    assert result["status"] == "completed"
    assert len(result["completedActions"]) == 1
    assert fake_planner.call_count == 1


@pytest.mark.asyncio
async def test_sequence_short_circuits_on_duplicate_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The planner proposes the same open with the same arguments twice.
    # The duplicate-action guard should finalize the run after the first
    # success instead of executing the same action again.
    _patch_planner(
        monkeypatch,
        [_open_action_result(), _open_action_result()],
    )

    session_id = str(uuid4())

    pending_open = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    assert pending_open["action"]["action"] == "open_cad_document"

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert result["status"] == "completed"
    assert len(result["completedActions"]) == 1
    assert (
        result["completedActions"][0]["action"]
        == "open_cad_document"
    )


@pytest.mark.asyncio
async def test_sequence_stops_immediately_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [_open_action_result(), _close_action_result()],
    )

    pending_open = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox, then close it.",
    )

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=False,
            errorCode="FILE_NOT_FOUND",
            errorMessage="The file could not be found.",
        ),
    )

    assert result["status"] == "execution_failed"
    assert result["errorCode"] == "FILE_NOT_FOUND"
    # No completed actions yet, since the very first one failed.
    assert result["completedActions"] == []


@pytest.mark.asyncio
async def test_sequence_stops_after_partial_success_then_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [_open_action_result(), _close_action_result()],
    )

    session_id = str(uuid4())

    pending_open = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox, then close it.",
    )

    pending_close = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    result = await resume_catia_agent(
        run_id=pending_close["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_close["actionId"],
            success=False,
            errorCode="CATIA_BUSY",
            errorMessage="CATIA could not close the document.",
        ),
    )

    assert result["status"] == "execution_failed"
    assert "CATIA could not close the document." in result["message"]
    assert len(result["completedActions"]) == 1


@pytest.mark.asyncio
async def test_sequence_stops_for_clarification_mid_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [
            _open_action_result(),
            ClarificationRequiredResult(
                status="clarification_required",
                message="Which document should be closed?",
                missingField="documentName",
            ),
        ],
    )

    pending_open = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox, then close a document.",
    )

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert result["status"] == "clarification_required"
    assert result["missingField"] == "documentName"
    assert result["message"] == "Which document should be closed?"
    assert len(result["completedActions"]) == 1


@pytest.mark.asyncio
async def test_sequence_stops_for_non_agentic_mid_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [
            _open_action_result(),
            NonAgenticResult(
                status="non_agentic",
                message=(
                    "This request does not require a CATIA action. "
                    "Route it to the general chat service."
                ),
            ),
        ],
    )

    pending_open = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox, then tell me a joke.",
    )

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert result["status"] == "non_agentic"
    assert len(result["completedActions"]) == 1
    assert (
        result["completedActions"][0]["message"]
        == "Gearbox.CATProduct was opened successfully."
    )


@pytest.mark.asyncio
async def test_sequence_no_decision_followup_returns_planning_error_for_multi_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(
        monkeypatch,
        [
            _open_action_result(),
            PlanningErrorResult(
                status="planning_error",
                errorCode="MODEL_RETURNED_NO_DECISION",
                message=(
                    "The Agent could not determine the next required "
                    "CATIA action for this request."
                ),
            ),
        ],
    )

    pending_open = await start_catia_agent(
        session_id=str(uuid4()),
        user_message="Open the gearbox, then close and download it.",
    )

    result = await resume_catia_agent(
        run_id=pending_open["runId"],
        action_result=ActionExecutionResultRequest(
            actionId=pending_open["actionId"],
            success=True,
            data={"documentName": "Gearbox.CATProduct"},
        ),
    )

    assert result["status"] == "planning_error"
    assert len(result["completedActions"]) == 1


@pytest.mark.asyncio
async def test_sequence_is_capped_at_max_actions_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.graph_nodes.settings.max_actions_per_run",
        2,
    )

    # Use distinct actions on each planning pass so the duplicate-action
    # guard does not short-circuit the loop before the cap is reached.
    distinct_open_results = [
        ActionRequiredResult(
            status="action_required",
            action=OpenCadDocumentAction.model_validate(
                {
                    "action": "open_cad_document",
                    "arguments": {
                        "filePath": f"C:\\TestData\\File{index}.CATProduct",
                    },
                }
            ),
        )
        for index in range(5)
    ]

    _patch_planner(monkeypatch, distinct_open_results)

    session_id = str(uuid4())

    pending = await start_catia_agent(
        session_id=session_id,
        user_message=(
            "Open one document, then open another, "
            "then open a third one."
        ),
    )

    for _ in range(2):
        pending = await resume_catia_agent(
            run_id=pending["runId"],
            action_result=ActionExecutionResultRequest(
                actionId=pending["actionId"],
                success=True,
                data={"documentName": "Gearbox.CATProduct"},
            ),
        )

    assert pending["status"] == "unsupported_request"
    assert "maximum number of CATIA actions" in pending["message"]
    assert len(pending["completedActions"]) == 2


# ---------------------------------------------------------
# Session robustness: concurrency, expiry, interruption
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_start_on_same_session_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_planner = asyncio.Event()

    async def slow_plan_catia_action(*args, **kwargs):
        await release_planner.wait()
        return _open_action_result()

    monkeypatch.setattr(
        "catia_agent.graph_nodes.plan_catia_action",
        slow_plan_catia_action,
    )

    session_id = str(uuid4())

    first = asyncio.create_task(
        start_catia_agent(
            session_id=session_id,
            user_message="Open the gearbox.",
        )
    )
    await asyncio.sleep(0)

    with pytest.raises(AgentRunStateConflictError):
        await start_catia_agent(
            session_id=session_id,
            user_message="Open the gearbox.",
        )

    release_planner.set()
    assert (await first)["status"] == "action_required"


@pytest.mark.asyncio
async def test_non_action_runs_do_not_leak_run_mapping(
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
        user_message="What is a pad?",
    )

    assert get_session_id_for_run(response["runId"]) is None


@pytest.mark.asyncio
async def test_expired_pending_run_releases_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())
    session_id = str(uuid4())

    stale = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    with pytest.raises(AgentRunStateConflictError):
        await start_catia_agent(
            session_id=session_id,
            user_message="Open the gearbox.",
        )

    monkeypatch.setattr(
        "catia_agent.graph_session.settings.pending_run_timeout_seconds",
        -1,
    )

    fresh = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    assert fresh["status"] == "action_required"
    assert fresh["runId"] != stale["runId"]

    with pytest.raises(AgentRunNotFoundError):
        await resume_catia_agent(
            run_id=stale["runId"],
            action_result=ActionExecutionResultRequest(
                actionId=stale["actionId"],
                success=True,
                data={"documentName": "Gearbox.CATProduct"},
            ),
        )


@pytest.mark.asyncio
async def test_resume_rejects_expired_pending_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())
    session_id = str(uuid4())

    pending = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    monkeypatch.setattr(
        "catia_agent.graph_session.settings.pending_run_timeout_seconds",
        -1,
    )

    with pytest.raises(AgentRunNotFoundError):
        await resume_catia_agent(
            run_id=pending["runId"],
            action_result=ActionExecutionResultRequest(
                actionId=pending["actionId"],
                success=True,
                data={"documentName": "Gearbox.CATProduct"},
            ),
        )

    assert get_session_id_for_run(pending["runId"]) is None


@pytest.mark.asyncio
async def test_interrupted_start_does_not_lock_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_planner(monkeypatch, _open_action_result())

    real_ainvoke = graph_module.catia_agent_graph.ainvoke
    calls = {"count": 0}

    # Simulate the request timeout firing right after the graph paused,
    # so the client never received the actionId.
    async def ainvoke_then_cancel_once(*args, **kwargs):
        result = await real_ainvoke(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(
        graph_module.catia_agent_graph,
        "ainvoke",
        ainvoke_then_cancel_once,
    )

    session_id = str(uuid4())

    with pytest.raises(asyncio.CancelledError):
        await start_catia_agent(
            session_id=session_id,
            user_message="Open the gearbox.",
        )

    response = await start_catia_agent(
        session_id=session_id,
        user_message="Open the gearbox.",
    )

    assert response["status"] == "action_required"

