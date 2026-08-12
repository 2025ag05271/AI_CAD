from __future__ import annotations

from typing import Any

from catia_agent.graph_classification import (
    _extract_predicate_count_signal,
)
from catia_agent.graph_responses import (
    _build_action_failure_message,
    _build_action_success_message,
    _join_completed_messages,
    _record_conversation_turn,
    _serialize_completed_actions,
)
from catia_agent.graph_state import (
    AgentRunStateConflictError,
    CatiaAgentState,
    TERMINAL_READ_ONLY_TOOLS,
)
from catia_agent.logging_config import get_logger
from catia_agent.models import ActionExecutionResultRequest


logger = get_logger(__name__)


def _finalize_completed_run(
    state: CatiaAgentState,
    completed_actions: list[dict[str, Any]],
    expected_action_id: str,
    result: ActionExecutionResultRequest,
) -> dict[str, Any]:
    """
    Build the graph-state update that ends a run as "completed" without
    another planner call. Shared by every short-circuit branch below.
    """

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

        return _finalize_completed_run(
            state,
            completed_actions,
            expected_action_id,
            result,
        )

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

        return _finalize_completed_run(
            state,
            completed_actions,
            expected_action_id,
            result,
        )

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

        return _finalize_completed_run(
            state,
            completed_actions,
            expected_action_id,
            result,
        )

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

        return _finalize_completed_run(
            state,
            completed_actions,
            expected_action_id,
            result,
        )

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
