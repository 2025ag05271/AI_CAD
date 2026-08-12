from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from catia_agent.graph_classification import (
    _classify_planning_mode,
)
from catia_agent.graph_completion import (
    record_action_result_node,
)
from catia_agent.graph_nodes import (
    await_action_result_node,
    plan_action_node,
    prepare_action_node,
)
from catia_agent.graph_responses import _new_identifier
from catia_agent.graph_session import (
    _build_current_response,
    _get_existing_state,
    _is_waiting_for_action_result,
    _run_sessions,
    _thread_config,
    get_session_id_for_run,
)
from catia_agent.graph_state import (
    AgentRunNotFoundError,
    AgentRunStateConflictError,
    CatiaAgentState,
    TERMINAL_READ_ONLY_TOOLS,
)
from catia_agent.graph_terminal import (
    prepare_sequence_completed_response_node,
    prepare_terminal_response_node,
)
from catia_agent.logging_config import (
    action_id_context,
    get_logger,
    run_id_context,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    CatiaContext,
    RequestCompletedResult,
)


logger = get_logger(__name__)

# Re-exported for backward compatibility: callers outside this module
# (app.py, tests) import these directly from catia_agent.graph. The
# actual definitions live in the graph_state/graph_session/
# graph_classification modules imported above, which were split out of
# this file to keep every graph_* module under ~400 lines. See those
# modules for the definitions of AgentRunStateConflictError,
# AgentRunNotFoundError, CatiaAgentState, TERMINAL_READ_ONLY_TOOLS, and
# get_session_id_for_run.


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
# Public entry points
# -------------------------------------------------------------------------

async def start_catia_agent(
    session_id: str,
    user_message: str,
    catia_context: CatiaContext | None = None,
) -> dict[str, Any]:
    existing_state = _get_existing_state(
        catia_agent_graph,
        session_id,
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
            catia_agent_graph,
            session_id,
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
        catia_agent_graph,
        session_id,
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
            catia_agent_graph,
            session_id,
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
