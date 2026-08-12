from __future__ import annotations

import json
import re
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from catia_agent.config import settings
from catia_agent.logging_config import (
    action_id_context,
    get_logger,
    run_id_context,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    CatiaAction,
    CatiaContext,
    ClarificationRequiredResult,
    InvalidRequestResult,
    NonAgenticResult,
    PlannerResult,
    PlanningErrorResult,
    RequestCompletedResult,
    UnsupportedRequestResult,
)
from catia_agent.planner import plan_catia_action


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Exceptions
# -------------------------------------------------------------------------

class AgentRunStateConflictError(ValueError):
    """
    Raised when a run request does not match the current graph state.
    """


class AgentRunNotFoundError(KeyError):
    """
    Raised when a run identifier is unknown to this process.
    """


# -------------------------------------------------------------------------
# Graph state
# -------------------------------------------------------------------------

class CatiaAgentState(TypedDict, total=False):
    session_id: str
    run_id: str
    action_id: str

    user_message: str
    catia_context: CatiaContext | None

    messages: list[dict[str, str]]
    sequence_progress: list[dict[str, str]]
    planning_mode: str
    planning_mode_confidence: str
    planning_mode_signals: list[str]
    planning_expected_actions: int | None

    planner_result: PlannerResult
    pending_action: CatiaAction
    action_result: ActionExecutionResultRequest

    completed_actions: list[dict[str, Any]]
    final_response: dict[str, Any]


# -------------------------------------------------------------------------
# Tool classification
# -------------------------------------------------------------------------

# CATIA tools whose success terminates the run without another planner call.
# These are pure reads that answer a question and never chain into a
# follow-up action. Add future read tools here as they land.
TERMINAL_READ_ONLY_TOOLS: frozenset[str] = frozenset()


# -------------------------------------------------------------------------
# Process-local run lookup
# -------------------------------------------------------------------------

_run_sessions: dict[str, str] = {}


# -------------------------------------------------------------------------
# Deterministic request-shape classification
#
# This replaces a previous spaCy dependency-parse classifier. spaCy added
# a ~30MB model download, non-trivial per-process load time, and its
# statistical POS tagging was not meaningfully more reliable than a
# lexical heuristic for this narrow job. It is not the ask-vs-agent
# decision maker: that decision is made exclusively by the model, via
# tool selection between route_to_general_chat and a CATIA tool (see
# _build_system_prompt in planner.py). This classifier only supplies a
# cheap, conservative signal for whether the follow-up planning loop in
# record_action_result_node can be skipped after the first action. Since
# the Azure-hosted model is materially more reliable at obeying the
# goal-delta follow-up prompting than the earlier small local model, this
# classifier can afford to be simple and err toward "follow up required"
# whenever it is unsure; the duplicate-action guard and silent-model
# recovery in plan_action_node remain the deterministic safety net
# regardless of classifier or model quality.
# -------------------------------------------------------------------------

_CATIA_VERB_PATTERN = re.compile(
    r"^(?:open|clos\w*|creat\w*|download\w*|start\w*|get\w*|list\w*)",
    re.IGNORECASE,
)

_SEQUENCE_CONNECTOR_SPLIT_PATTERN = re.compile(
    r"\b(?:then|after\s+that|afterwards|followed\s+by|next)\b|;|\band\b",
    re.IGNORECASE,
)

_LEADING_FILLER_PATTERN = re.compile(
    r"""^(?:
        [\s,.:;!-]+
        |cool\b
        |okay\b|ok\b
        |please\b
        |kindly\b
        |just\b
        |now\b
        |well\b
        |alright\b
        |so\b
        |(?:can|could|would|will)\s+(?:you|i|we)?\s*(?:please\s+)?
        |i\s+(?:want|need|would\s+like)\s+to\s+
        |i'?d\s+like\s+to\s+
        |let'?s\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _strip_leading_filler(
    segment: str,
) -> str:
    previous_segment = None
    current_segment = segment.strip()

    while previous_segment != current_segment:
        previous_segment = current_segment
        current_segment = _LEADING_FILLER_PATTERN.sub(
            "",
            current_segment,
            count=1,
        ).strip()

    return current_segment


def _split_into_clauses(
    user_message: str,
) -> list[str]:
    segments = _SEQUENCE_CONNECTOR_SPLIT_PATTERN.split(
        user_message
    )

    return [
        segment
        for segment in segments
        if segment and segment.strip()
    ]


def _clause_starts_with_catia_verb(
    clause: str,
) -> bool:
    cleaned_clause = _strip_leading_filler(clause)

    return (
        _CATIA_VERB_PATTERN.match(cleaned_clause)
        is not None
    )


def _extract_predicate_count_signal(
    signals: list[str],
) -> int | None:
    prefix = "multiple_action_verbs:"

    for signal in signals:
        if not signal.startswith(prefix):
            continue

        try:
            return int(
                signal.split(":", maxsplit=1)[1]
            )

        except ValueError:
            return None

    return None


def _classify_planning_mode(
    user_message: str,
) -> tuple[str, str, list[str], int | None]:
    normalized_message = user_message.strip()

    if not normalized_message:
        return (
            "follow_up_required",
            "low",
            ["empty_message"],
            None,
        )

    clauses = _split_into_clauses(normalized_message)
    has_connector = len(clauses) > 1

    matched_action_count = sum(
        1
        for clause in clauses
        if _clause_starts_with_catia_verb(clause)
    )

    if matched_action_count == 0:
        return (
            "follow_up_required",
            "low",
            ["no_catia_verbs"],
            None,
        )

    if matched_action_count > 1:
        return (
            "follow_up_required",
            "high",
            [
                f"expected_catia_actions:{matched_action_count}",
                f"multiple_action_verbs:{matched_action_count}",
            ],
            matched_action_count,
        )

    if has_connector:
        return (
            "single_action_fast_path",
            "medium",
            [
                "expected_catia_actions:1",
                "mixed_predicates_single_catia_intent",
            ],
            1,
        )

    return (
        "single_action_fast_path",
        "high",
        [
            "expected_catia_actions:1",
            "single_action_fast_path",
        ],
        1,
    )


def get_session_id_for_run(
    run_id: str,
) -> str | None:
    return _run_sessions.get(run_id)


def _thread_config(
    session_id: str,
) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": session_id,
        }
    }


# -------------------------------------------------------------------------
# Response helpers
# -------------------------------------------------------------------------

def _new_identifier() -> str:
    return str(uuid4())


def _serialize_completed_actions(
    completed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(action)
        for action in completed_actions
    ]


def _completed_actions_or_none(
    completed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if not completed_actions:
        return None

    return _serialize_completed_actions(
        completed_actions
    )


def _join_completed_messages(
    completed_actions: list[dict[str, Any]],
) -> str:
    messages = [
        str(action.get("message", "")).strip()
        for action in completed_actions
        if str(action.get("message", "")).strip()
    ]

    if not messages:
        return (
            "The requested CATIA actions completed "
            "successfully."
        )

    return " ".join(messages)


def _record_conversation_turn(
    state: CatiaAgentState,
    assistant_message: str,
) -> list[dict[str, str]]:
    messages = list(
        state.get("messages", [])
    )

    messages.append(
        {
            "role": "user",
            "content": state["user_message"],
        }
    )

    messages.append(
        {
            "role": "assistant",
            "content": assistant_message,
        }
    )

    maximum_messages = (
        settings.max_conversation_history_messages
    )

    return messages[-maximum_messages:]


def _build_action_success_message(
    action_name: str,
    result_data: dict[str, Any] | None,
) -> str:
    data = result_data or {}

    if action_name == "open_cad_document":
        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"{document_name} was opened successfully."
            )

        return (
            "The CATIA document was opened successfully."
        )

    if action_name == "get_active_document":
        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"The active CATIA document is "
                f"{document_name}."
            )

        return (
            "CATIA does not currently have an active "
            "document."
        )

    if action_name == "create_cad_document":
        document_name = data.get(
            "documentName"
        )

        document_type = data.get(
            "documentType"
        )

        if document_name:
            return (
                f"{document_name} was created successfully."
            )

        if document_type:
            return (
                f"A new {document_type} document was "
                "created successfully."
            )

        return (
            "The CATIA document was created successfully."
        )

    if action_name == "close_cad_document":
        closed_documents = data.get(
            "closedDocuments"
        )

        if isinstance(
            closed_documents,
            list,
        ) and closed_documents:
            names = ", ".join(
                str(name)
                for name in closed_documents
            )

            return (
                f"{names} closed successfully."
            )

        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"{document_name} closed successfully."
            )

        return (
            "The requested CATIA document was closed "
            "successfully."
        )

    if action_name == "download_cad":
        document_name = data.get(
            "documentName"
        )

        part_number = data.get(
            "partNumber"
        )

        part_revision = data.get(
            "partRevision"
        )

        if document_name:
            return (
                f"{document_name} was downloaded successfully."
            )

        if part_number and part_revision:
            return (
                f"{part_number} revision {part_revision} was "
                "downloaded successfully."
            )

        if part_number:
            return (
                f"{part_number} was downloaded successfully."
            )

        return (
            "The CAD file was downloaded successfully."
        )

    if action_name == "start_model":
        success_message = data.get(
            "success_message"
        )

        if success_message:
            return str(success_message)

        return (
            "The CATIA start model was launched successfully."
        )

    return (
        "The CATIA action completed successfully."
    )


def _build_action_failure_message(
    action_name: str,
    result: ActionExecutionResultRequest,
) -> str:
    error_message = (
        result.errorMessage
        or "The external CATIA executor reported a failure."
    )

    return (
        f"The CATIA action '{action_name}' failed. "
        f"{error_message}"
    )


# -------------------------------------------------------------------------
# Graph nodes
# -------------------------------------------------------------------------

async def plan_action_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    completed_actions = list(
        state.get("completed_actions", [])
    )

    if (
        len(completed_actions)
        >= settings.max_actions_per_run
    ):
        return {
            "planner_result": (
                UnsupportedRequestResult(
                    status="unsupported_request",
                    message=(
                        "The maximum number of CATIA actions "
                        "allowed for one request was reached."
                    ),
                    requestedCapability=None,
                    reason="action_limit_reached",
                )
            )
        }

    conversation_history = [
        *state.get("messages", []),
        *state.get(
            "sequence_progress",
            [],
        ),
    ]

    planner_result = await plan_catia_action(
        user_command=state["user_message"],
        catia_context=state.get(
            "catia_context"
        ),
        conversation_history=conversation_history,
        completed_actions=completed_actions,
        planning_mode=state.get(
            "planning_mode"
        ),
        planning_mode_confidence=state.get(
            "planning_mode_confidence"
        ),
    )

    duplicate = _find_duplicate_completed_action(
        planner_result,
        completed_actions,
    )

    if duplicate is not None:
        logger.warning(
            "planner_duplicate_action_detected",
            extra={
                "runId": state["run_id"],
                "selectedTool": duplicate["action"],
            },
        )

        retry_hint = _build_duplicate_retry_hint(
            duplicate
        )

        planner_result = await plan_catia_action(
            user_command=state["user_message"],
            catia_context=state.get(
                "catia_context"
            ),
            conversation_history=conversation_history,
            completed_actions=completed_actions,
            retry_hint=retry_hint,
            planning_mode=state.get(
                "planning_mode"
            ),
            planning_mode_confidence=state.get(
                "planning_mode_confidence"
            ),
        )

        duplicate = _find_duplicate_completed_action(
            planner_result,
            completed_actions,
        )

        if duplicate is not None:
            logger.warning(
                "planner_duplicate_retry_failed",
                extra={
                    "runId": state["run_id"],
                    "selectedTool": duplicate["action"],
                },
            )

            planner_result = RequestCompletedResult(
                status="request_completed"
            )

        else:
            logger.info(
                "planner_duplicate_retry_recovered",
                extra={
                    "runId": state["run_id"],
                    "selectedTool": (
                        planner_result.action.action
                        if isinstance(
                            planner_result,
                            ActionRequiredResult,
                        )
                        else None
                    ),
                },
            )

    return {
        "planner_result": planner_result,
    }


def _find_duplicate_completed_action(
    planner_result: PlannerResult,
    completed_actions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(
        planner_result,
        ActionRequiredResult,
    ):
        return None

    proposed = planner_result.action.model_dump(
        mode="json"
    )

    for entry in completed_actions:
        if (
            entry.get("action") == proposed["action"]
            and entry.get("arguments", {})
            == proposed["arguments"]
        ):
            return {
                "action": proposed["action"],
                "arguments": proposed["arguments"],
            }

    return None


def _build_duplicate_retry_hint(
    duplicate: dict[str, Any],
) -> str:
    arguments_json = json.dumps(
        duplicate["arguments"],
        sort_keys=True,
    )

    return (
        "Duplicate detected in this planning step.\n\n"
        "You just selected "
        f"{duplicate['action']} with arguments {arguments_json}, "
        "which is IDENTICAL to an action already listed in the "
        "goal-delta analysis above as completed.\n\n"
        "Redo the goal-delta analysis:\n"
        "1. Look at the original user request shown above.\n"
        "2. Look at the actions already completed.\n"
        "3. Identify what CATIA operation is still not done.\n\n"
        "Select the SINGLE NEXT remaining CATIA action. If every "
        "CATIA operation in the original request is done, call "
        "complete_request. Do not repeat the action shown above."
    )


def prepare_action_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    planner_result = state["planner_result"]

    if not isinstance(
        planner_result,
        ActionRequiredResult,
    ):
        raise AgentRunStateConflictError(
            "The graph attempted to prepare an action "
            "without an action-required planner result."
        )

    action_id = _new_identifier()

    return {
        "action_id": action_id,
        "pending_action": planner_result.action,
        "action_result": None,
    }


def await_action_result_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    pending_action = state["pending_action"]

    resume_payload = interrupt(
        {
            "status": "action_required",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": state["action_id"],
            "action": pending_action.model_dump(
                mode="json"
            ),
        }
    )

    result = (
        ActionExecutionResultRequest.model_validate(
            resume_payload
        )
    )

    return {
        "action_result": result,
    }


def record_action_result_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    result = state["action_result"]
    pending_action = state["pending_action"]

    expected_action_id = state["action_id"]

    if result.actionId != expected_action_id:
        raise AgentRunStateConflictError(
            "The submitted actionId does not match "
            "the action currently awaiting a result."
        )

    completed_actions = list(
        state.get("completed_actions", [])
    )

    action_name = pending_action.action
    action_arguments = (
        pending_action.arguments.model_dump(
            mode="json"
        )
    )

    if not result.success:
        failure_message = (
            _build_action_failure_message(
                action_name,
                result,
            )
        )

        final_response = {
            "status": "execution_failed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "message": failure_message,
            "errorCode": result.errorCode,
            "errorMessage": result.errorMessage,
            "completedActions": (
                _serialize_completed_actions(
                    completed_actions
                )
            ),
        }

        return {
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state,
                failure_message,
            ),
        }

    success_message = (
        _build_action_success_message(
            action_name,
            result.data,
        )
    )

    completed_action = {
        "action": action_name,
        "actionId": expected_action_id,
        "arguments": action_arguments,
        "message": success_message,
        "data": result.data,
    }

    completed_actions.append(
        completed_action
    )

    logger.info(
        "action_result_recorded",
        extra={
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "selectedTool": action_name,
            "completedActionCount": len(
                completed_actions
            ),
            "planningMode": state.get(
                "planning_mode"
            ),
            "planningModeConfidence": state.get(
                "planning_mode_confidence"
            ),
        },
    )

    sequence_progress = list(
        state.get("sequence_progress", [])
    )

    sequence_progress.append(
        {
            "role": "assistant",
            "content": success_message,
        }
    )

    if action_name in TERMINAL_READ_ONLY_TOOLS:
        logger.info(
            "planner_skipped_terminal_read",
            extra={
                "runId": state["run_id"],
                "selectedTool": action_name,
            },
        )

        final_message = _join_completed_messages(
            completed_actions
        )

        final_response = {
            "status": "completed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "message": final_message,
            "data": result.data,
            "completedActions": (
                _serialize_completed_actions(
                    completed_actions
                )
            ),
        }

        return {
            "completed_actions": completed_actions,
            "sequence_progress": [],
            "planner_result": None,
            "pending_action": None,
            "action_result": None,
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state,
                final_message,
            ),
        }

    is_single_fast_path = (
        state.get("planning_mode")
        == "single_action_fast_path"
    )

    is_high_confidence = (
        state.get("planning_mode_confidence")
        == "high"
    )

    is_first_successful_action = (
        len(completed_actions) == 1
    )

    if (
        is_single_fast_path
        and is_high_confidence
        and is_first_successful_action
    ):
        logger.info(
            "planner_followup_skipped_single_action",
            extra={
                "runId": state["run_id"],
                "selectedTool": action_name,
                "planningModeSignals": state.get(
                    "planning_mode_signals",
                    [],
                ),
            },
        )

        final_message = _join_completed_messages(
            completed_actions
        )

        final_response = {
            "status": "completed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "message": final_message,
            "data": result.data,
            "completedActions": (
                _serialize_completed_actions(
                    completed_actions
                )
            ),
        }

        return {
            "completed_actions": completed_actions,
            "sequence_progress": [],
            "planner_result": None,
            "pending_action": None,
            "action_result": None,
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state,
                final_message,
            ),
        }

    expected_action_count = state.get(
        "planning_expected_actions"
    )

    planning_mode_signals = state.get(
        "planning_mode_signals",
        [],
    )

    is_medium_confidence = (
        state.get("planning_mode_confidence")
        == "medium"
    )

    if (
        is_single_fast_path
        and is_medium_confidence
        and is_first_successful_action
        and action_name == "start_model"
        and expected_action_count == 1
        and "mixed_predicates_single_catia_intent"
        in planning_mode_signals
    ):
        logger.info(
            "planner_followup_skipped_single_action",
            extra={
                "runId": state["run_id"],
                "selectedTool": action_name,
                "planningModeSignals": planning_mode_signals,
            },
        )

        final_message = _join_completed_messages(
            completed_actions
        )

        final_response = {
            "status": "completed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "message": final_message,
            "data": result.data,
            "completedActions": (
                _serialize_completed_actions(
                    completed_actions
                )
            ),
        }

        return {
            "completed_actions": completed_actions,
            "sequence_progress": [],
            "planner_result": None,
            "pending_action": None,
            "action_result": None,
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state,
                final_message,
            ),
        }

    is_high_confidence_follow_up = (
        state.get("planning_mode")
        == "follow_up_required"
        and is_high_confidence
    )

    if (
        is_high_confidence_follow_up
        and expected_action_count is not None
        and expected_action_count > 0
        and (
            _extract_predicate_count_signal(
                state.get(
                    "planning_mode_signals",
                    [],
                )
            )
            == expected_action_count
        )
        and len(completed_actions)
        >= expected_action_count
    ):
        logger.info(
            "planner_followup_skipped_expected_actions_exhausted",
            extra={
                "runId": state["run_id"],
                "selectedTool": action_name,
                "expectedActionCount": expected_action_count,
                "completedActionCount": len(
                    completed_actions
                ),
                "planningModeSignals": state.get(
                    "planning_mode_signals",
                    [],
                ),
            },
        )

        final_message = _join_completed_messages(
            completed_actions
        )

        final_response = {
            "status": "completed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": expected_action_id,
            "message": final_message,
            "data": result.data,
            "completedActions": (
                _serialize_completed_actions(
                    completed_actions
                )
            ),
        }

        return {
            "completed_actions": completed_actions,
            "sequence_progress": [],
            "planner_result": None,
            "pending_action": None,
            "action_result": None,
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state,
                final_message,
            ),
        }

    logger.info(
        "planner_followup_required",
        extra={
            "runId": state["run_id"],
            "selectedTool": action_name,
            "completedActionCount": len(
                completed_actions
            ),
            "planningMode": state.get(
                "planning_mode"
            ),
            "planningModeConfidence": state.get(
                "planning_mode_confidence"
            ),
        },
    )

    return {
        "completed_actions": completed_actions,
        "sequence_progress": sequence_progress,
        "planner_result": None,
        "pending_action": None,
        "action_result": None,
        "final_response": None,
    }


def prepare_sequence_completed_response_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    completed_actions = list(
        state.get("completed_actions", [])
    )

    message = _join_completed_messages(
        completed_actions
    )

    last_action_id = (
        completed_actions[-1]["actionId"]
        if completed_actions
        else None
    )

    last_data = (
        completed_actions[-1].get("data")
        if completed_actions
        else None
    )

    response = {
        "status": "completed",
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "actionId": last_action_id,
        "message": message,
        "data": last_data,
        "completedActions": (
            _serialize_completed_actions(
                completed_actions
            )
        ),
    }

    return {
        "final_response": response,
        "messages": _record_conversation_turn(
            state,
            message,
        ),
        "sequence_progress": [],
    }


def prepare_terminal_response_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    planner_result = state["planner_result"]

    completed_actions = list(
        state.get("completed_actions", [])
    )

    base_response: dict[str, Any] = {
        "sessionId": state["session_id"],
        "runId": state["run_id"],
    }

    if isinstance(
        planner_result,
        NonAgenticResult,
    ):
        response = {
            **base_response,
            **planner_result.model_dump(
                mode="json"
            ),
        }

    elif isinstance(
        planner_result,
        ClarificationRequiredResult,
    ):
        response = {
            **base_response,
            **planner_result.model_dump(
                mode="json"
            ),
        }

    elif isinstance(
        planner_result,
        UnsupportedRequestResult,
    ):
        response = {
            **base_response,
            **planner_result.model_dump(
                mode="json"
            ),
        }

    elif isinstance(
        planner_result,
        InvalidRequestResult,
    ):
        response = {
            **base_response,
            **planner_result.model_dump(
                mode="json"
            ),
        }

    elif isinstance(
        planner_result,
        PlanningErrorResult,
    ):
        response = {
            **base_response,
            **planner_result.model_dump(
                mode="json"
            ),
        }

    else:
        raise AgentRunStateConflictError(
            "The graph reached terminal-response "
            "preparation with an unsupported planner result."
        )

    completed_response_actions = (
        _completed_actions_or_none(
            completed_actions
        )
    )

    if completed_response_actions is not None:
        response["completedActions"] = (
            completed_response_actions
        )

    message = str(
        response.get(
            "message",
            "The Agent run ended.",
        )
    )

    return {
        "final_response": response,
        "messages": _record_conversation_turn(
            state,
            message,
        ),
        "sequence_progress": [],
    }


# -------------------------------------------------------------------------
# Conditional routing
# -------------------------------------------------------------------------

def route_after_planning(
    state: CatiaAgentState,
) -> str:
    planner_result = state["planner_result"]

    if isinstance(
        planner_result,
        ActionRequiredResult,
    ):
        return "prepare_action"

    if isinstance(
        planner_result,
        RequestCompletedResult,
    ):
        return "prepare_sequence_completed"

    return "prepare_terminal_response"


def route_after_action_result(
    state: CatiaAgentState,
) -> str:
    if state.get("final_response") is not None:
        return "stop"

    return "continue"


# -------------------------------------------------------------------------
# Graph construction
# -------------------------------------------------------------------------

graph_builder = StateGraph(
    CatiaAgentState
)

graph_builder.add_node(
    "plan_action",
    plan_action_node,
)

graph_builder.add_node(
    "prepare_action",
    prepare_action_node,
)

graph_builder.add_node(
    "await_action_result",
    await_action_result_node,
)

graph_builder.add_node(
    "record_action_result",
    record_action_result_node,
)

graph_builder.add_node(
    "prepare_sequence_completed",
    prepare_sequence_completed_response_node,
)

graph_builder.add_node(
    "prepare_terminal_response",
    prepare_terminal_response_node,
)

graph_builder.add_edge(
    START,
    "plan_action",
)

graph_builder.add_conditional_edges(
    "plan_action",
    route_after_planning,
    {
        "prepare_action": "prepare_action",
        "prepare_sequence_completed": (
            "prepare_sequence_completed"
        ),
        "prepare_terminal_response": (
            "prepare_terminal_response"
        ),
    },
)

graph_builder.add_edge(
    "prepare_action",
    "await_action_result",
)

graph_builder.add_edge(
    "await_action_result",
    "record_action_result",
)

graph_builder.add_conditional_edges(
    "record_action_result",
    route_after_action_result,
    {
        "continue": "plan_action",
        "stop": END,
    },
)

graph_builder.add_edge(
    "prepare_sequence_completed",
    END,
)

graph_builder.add_edge(
    "prepare_terminal_response",
    END,
)


checkpointer = InMemorySaver()

catia_agent_graph = graph_builder.compile(
    checkpointer=checkpointer
)


# -------------------------------------------------------------------------
# State helpers
# -------------------------------------------------------------------------

def _get_existing_state(
    session_id: str,
) -> dict[str, Any]:
    snapshot = catia_agent_graph.get_state(
        _thread_config(session_id)
    )

    if snapshot is None:
        return {}

    return dict(snapshot.values or {})


def _is_waiting_for_action_result(
    state: dict[str, Any],
) -> bool:
    return bool(
        state.get("pending_action")
        and state.get("action_id")
        and state.get("final_response") is None
    )


def _build_current_response(
    state: dict[str, Any],
) -> dict[str, Any]:
    final_response = state.get(
        "final_response"
    )

    if final_response is not None:
        return dict(final_response)

    pending_action = state.get(
        "pending_action"
    )

    action_id = state.get(
        "action_id"
    )

    if pending_action is not None and action_id:
        action_payload = (
            pending_action.model_dump(
                mode="json"
            )
            if hasattr(
                pending_action,
                "model_dump",
            )
            else pending_action
        )

        return {
            "status": "action_required",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": action_id,
            "action": action_payload,
        }

    raise AgentRunStateConflictError(
        "The Agent graph stopped without producing "
        "a terminal response or pending action."
    )


# -------------------------------------------------------------------------
# Public entry points
# -------------------------------------------------------------------------

async def start_catia_agent(
    session_id: str,
    user_message: str,
    catia_context: CatiaContext | None = None,
) -> dict[str, Any]:
    existing_state = _get_existing_state(
        session_id
    )

    if _is_waiting_for_action_result(
        existing_state
    ):
        raise AgentRunStateConflictError(
            "This session already has an Agent run "
            "waiting for a CATIA action result."
        )

    run_id = _new_identifier()

    _run_sessions[run_id] = session_id

    run_token = run_id_context.set(
        run_id
    )

    (
        planning_mode,
        planning_mode_confidence,
        planning_mode_signals,
        planning_expected_actions,
    ) = _classify_planning_mode(
        user_message
    )

    logger.info(
        "planning_mode_classified",
        extra={
            "runId": run_id,
            "mode": planning_mode,
            "confidence": planning_mode_confidence,
            "signals": planning_mode_signals,
            "expectedActionCount": (
                planning_expected_actions
            ),
        },
    )

    try:
        initial_state: CatiaAgentState = {
            "session_id": session_id,
            "run_id": run_id,
            "user_message": user_message.strip(),
            "catia_context": catia_context,
            "messages": list(
                existing_state.get(
                    "messages",
                    [],
                )
            ),
            "sequence_progress": [],
            "planning_mode": planning_mode,
            "planning_mode_confidence": (
                planning_mode_confidence
            ),
            "planning_mode_signals": planning_mode_signals,
            "planning_expected_actions": (
                planning_expected_actions
            ),
            "completed_actions": [],
            "final_response": None,
        }

        await catia_agent_graph.ainvoke(
            initial_state,
            config=_thread_config(
                session_id
            ),
        )

        current_state = _get_existing_state(
            session_id
        )

        return _build_current_response(
            current_state
        )

    finally:
        run_id_context.reset(
            run_token
        )


async def resume_catia_agent(
    run_id: str,
    action_result: ActionExecutionResultRequest,
) -> dict[str, Any]:
    session_id = _run_sessions.get(
        run_id
    )

    if session_id is None:
        raise AgentRunNotFoundError(
            f"No Agent run was found for runId "
            f"'{run_id}'."
        )

    existing_state = _get_existing_state(
        session_id
    )

    if existing_state.get(
        "run_id"
    ) != run_id:
        raise AgentRunStateConflictError(
            "The supplied runId does not match the "
            "current run for this session."
        )

    if not _is_waiting_for_action_result(
        existing_state
    ):
        raise AgentRunStateConflictError(
            "The Agent run is not currently waiting "
            "for a CATIA action result."
        )

    expected_action_id = existing_state.get(
        "action_id"
    )

    if action_result.actionId != expected_action_id:
        raise AgentRunStateConflictError(
            "The submitted actionId does not match "
            "the pending CATIA action."
        )

    run_token = run_id_context.set(
        run_id
    )

    action_token = action_id_context.set(
        action_result.actionId
    )

    try:
        await catia_agent_graph.ainvoke(
            Command(
                resume=action_result.model_dump(
                    mode="json"
                )
            ),
            config=_thread_config(
                session_id
            ),
        )

        current_state = _get_existing_state(
            session_id
        )

        response = _build_current_response(
            current_state
        )

        if response["status"] != "action_required":
            _run_sessions.pop(
                run_id,
                None,
            )

        return response

    finally:
        action_id_context.reset(
            action_token
        )

        run_id_context.reset(
            run_token
        )