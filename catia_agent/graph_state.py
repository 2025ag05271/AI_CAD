from __future__ import annotations

from typing import Any, TypedDict

from catia_agent.models import (
    ActionExecutionResultRequest,
    CatiaAction,
    CatiaContext,
    PlannerResult,
)


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

    # Set once in start_catia_agent from the embedding intent
    # classifier; None means the classifier did not run or could not
    # decide, so plan_action_node lets the model decide as before.
    is_agentic_request: bool | None

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
