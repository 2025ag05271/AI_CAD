from __future__ import annotations

from typing import Any

from catia_agent.graph_responses import (
    _completed_actions_or_none,
    _join_completed_messages,
    _record_conversation_turn,
    _serialize_completed_actions,
)
from catia_agent.graph_state import (
    AgentRunStateConflictError,
    CatiaAgentState,
)
from catia_agent.models import (
    ClarificationRequiredResult,
    InvalidRequestResult,
    NonAgenticResult,
    PlanningErrorResult,
    UnsupportedRequestResult,
)


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
