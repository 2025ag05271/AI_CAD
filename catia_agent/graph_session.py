from __future__ import annotations

from typing import Any

from catia_agent.graph_state import AgentRunStateConflictError


# -------------------------------------------------------------------------
# Process-local run lookup
# -------------------------------------------------------------------------

_run_sessions: dict[str, str] = {}


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
# State helpers
# -------------------------------------------------------------------------

def _get_existing_state(
    compiled_graph: Any,
    session_id: str,
) -> dict[str, Any]:
    snapshot = compiled_graph.get_state(
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
