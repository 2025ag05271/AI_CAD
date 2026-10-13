from __future__ import annotations

import inspect
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from catia_agent import models as catia_models
from catia_agent.config import settings
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
    _abandon_pending_run,
    _abandon_run_if_paused,
    _build_current_response,
    _exclusive_session,
    _forget_run_unless_paused,
    _get_existing_state,
    _is_pending_action_expired,
    _is_waiting_for_action_result,
    _release_or_reject_pending_run,
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
from catia_agent.intent_classifier import is_agentic_request
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

# AgentRunStateConflictError, AgentRunNotFoundError, CatiaAgentState,
# TERMINAL_READ_ONLY_TOOLS and get_session_id_for_run are re-exported
# here for app.py and tests; they live in the graph_* modules above.


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


# Every Pydantic model class stored in CatiaAgentState (CatiaContext,
# PlannerResult/CatiaAction variants and their nested arguments,
# ActionExecutionResultRequest, ...) lives in catia_agent.models, so
# allowlisting the whole module keeps this in sync automatically as
# new action/result types are added there.
_ALLOWED_MSGPACK_MODULES = tuple(
    cls
    for _, cls in inspect.getmembers(
        catia_models,
        inspect.isclass,
    )
    if cls.__module__ == catia_models.__name__
)

checkpointer = InMemorySaver(
    serde=JsonPlusSerializer(
        allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES
    )
)

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
    async with _exclusive_session(session_id):
        return await _start_run(
            session_id,
            user_message,
            catia_context,
        )


async def _start_run(
    session_id: str,
    user_message: str,
    catia_context: CatiaContext | None,
) -> dict[str, Any]:
    existing_state = _get_existing_state(
        catia_agent_graph,
        session_id,
    )

    _release_or_reject_pending_run(
        catia_agent_graph,
        session_id,
        existing_state,
    )

    run_id = _new_identifier()

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
        is_agentic = await is_agentic_request(
            user_message
        )

        has_explicit_action_shape = (
            planning_mode_confidence == "high"
            and planning_expected_actions is not None
        )
        if is_agentic is False and has_explicit_action_shape:
            logger.info(
                "embedding_classifier_overridden_by_explicit_action_shape",
                extra={
                    "runId": run_id,
                    "planningMode": planning_mode,
                    "planningModeConfidence": planning_mode_confidence,
                    "expectedActionCount": planning_expected_actions,
                },
            )
            is_agentic = True

        initial_state: CatiaAgentState = {
            "session_id": session_id,
            "run_id": run_id,
            "user_message": user_message.strip(),
            "catia_context": catia_context,
            "is_agentic_request": is_agentic,
            "messages": (
                list(
                    existing_state.get(
                        "messages",
                        [],
                    )
                )
                if settings.enable_conversation_history
                else []
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
            # Clear leftovers from the previous run on this thread so a
            # failure mid-run can't make the session look paused.
            "planner_result": None,
            "pending_action": None,
            "action_id": None,
            "action_result": None,
            "action_requested_at": None,
        }

        try:
            await catia_agent_graph.ainvoke(
                initial_state,
                config=_thread_config(
                    session_id
                ),
            )

        except BaseException:
            # e.g. the request timeout cancelled us after the graph had
            # already paused: the client never got the actionId.
            _abandon_run_if_paused(
                catia_agent_graph,
                session_id,
                run_id,
            )
            raise

        current_state = _get_existing_state(
            catia_agent_graph,
            session_id,
        )

        response = _build_current_response(
            current_state
        )

        if response["status"] == "action_required":
            _run_sessions[run_id] = session_id

        return response

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

    async with _exclusive_session(session_id):
        return await _resume_run(
            run_id,
            session_id,
            action_result,
        )


async def _resume_run(
    run_id: str,
    session_id: str,
    action_result: ActionExecutionResultRequest,
) -> dict[str, Any]:
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

    if _is_pending_action_expired(existing_state):
        _abandon_pending_run(
            catia_agent_graph,
            session_id,
            existing_state,
            reason="expired",
        )

        raise AgentRunNotFoundError(
            f"The Agent run '{run_id}' expired after waiting "
            f"more than {settings.pending_run_timeout_seconds} "
            "seconds for a CATIA action result."
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

        except BaseException:
            _forget_run_unless_paused(
                catia_agent_graph,
                session_id,
                run_id,
            )
            raise

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
