from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from catia_agent.config import settings
from catia_agent.graph_state import AgentRunStateConflictError
from catia_agent.logging_config import get_logger


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Process-local run lookup
# -------------------------------------------------------------------------

_run_sessions: dict[str, str] = {}

# Weak values: a session's lock is dropped once no request holds it.
_session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


@asynccontextmanager
async def _exclusive_session(
    session_id: str,
) -> AsyncIterator[None]:
    """
    Serialize requests per session, failing fast instead of queueing
    so a double-submitted request gets a 409 rather than a second run.
    """

    lock = _session_locks.get(session_id)

    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock

    if lock.locked():
        raise AgentRunStateConflictError(
            "Another request for this session is already "
            "in progress."
        )

    async with lock:
        yield


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


def _is_pending_action_expired(
    state: dict[str, Any],
) -> bool:
    requested_at = state.get("action_requested_at")

    return (
        requested_at is not None
        and time.time() - requested_at
        > settings.pending_run_timeout_seconds
    )


def _abandon_pending_run(
    compiled_graph: Any,
    session_id: str,
    state: dict[str, Any],
    reason: str,
) -> None:
    """
    Release a session whose pending action will never be reported.

    Synchronous on purpose: it runs from cancellation handlers, where
    any further await would itself be cancelled.
    """

    compiled_graph.update_state(
        _thread_config(session_id),
        {
            "pending_action": None,
            "action_id": None,
            "action_requested_at": None,
        },
        as_node="prepare_terminal_response",
    )

    run_id = state.get("run_id")

    if run_id is not None:
        _run_sessions.pop(run_id, None)

    logger.warning(
        "pending_run_abandoned",
        extra={
            "runId": run_id,
            "actionId": state.get("action_id"),
            "reason": reason,
        },
    )


def _release_or_reject_pending_run(
    compiled_graph: Any,
    session_id: str,
    state: dict[str, Any],
) -> None:
    if not _is_waiting_for_action_result(state):
        return

    if not _is_pending_action_expired(state):
        raise AgentRunStateConflictError(
            "This session already has an Agent run "
            "waiting for a CATIA action result."
        )

    _abandon_pending_run(
        compiled_graph,
        session_id,
        state,
        reason="expired",
    )


def _abandon_run_if_paused(
    compiled_graph: Any,
    session_id: str,
    run_id: str,
) -> None:
    state = _get_existing_state(compiled_graph, session_id)

    if (
        state.get("run_id") == run_id
        and _is_waiting_for_action_result(state)
    ):
        _abandon_pending_run(
            compiled_graph,
            session_id,
            state,
            reason="start_interrupted",
        )


def _forget_run_unless_paused(
    compiled_graph: Any,
    session_id: str,
    run_id: str,
) -> None:
    # Keep the mapping only while the client can still retry the same
    # action result.
    state = _get_existing_state(compiled_graph, session_id)

    if not _is_waiting_for_action_result(state):
        _run_sessions.pop(run_id, None)


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
