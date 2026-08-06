from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from catia_agent.config import settings
from catia_agent.logging_config import get_logger
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    CatiaContext,
    InvalidRequestResult,
    PlannerResult,
    ClarificationRequiredResult,
    UnsupportedRequestResult,
)
from catia_agent.planner import plan_catia_action


logger = get_logger(__name__)


# ---------------------------------------------------------
# Errors
# ---------------------------------------------------------

class AgentRunStateConflictError(ValueError):
    """Raised when an Agent run request does not match the current graph state."""


# ---------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------

class CatiaAgentState(TypedDict, total=False):
    session_id: str
    run_id: str
    action_id: str
    user_message: str

    catia_context: CatiaContext

    messages: list[dict[str, str]]

    planner_result: PlannerResult

    pending_action: dict[str, Any]
    action_result: dict[str, Any]

    # Multi-action sequences within a single run (see plan_action_node).
    completed_actions: list[dict[str, Any]]
    sequence_progress: list[dict[str, str]]
    sequence_capped: bool

    final_response: dict[str, Any]


# ---------------------------------------------------------
# Conversation history helper
# ---------------------------------------------------------

def _record_conversation_turn(
    state: CatiaAgentState,
    assistant_message: str,
) -> list[dict[str, str]]:
    """Append this turn so the next planner call sees it as history."""

    history = list(state.get("messages", []))

    history.append(
        {"role": "user", "content": state["user_message"]}
    )
    history.append(
        {"role": "assistant", "content": assistant_message}
    )

    return history


def _summarize_completed_actions(
    completed_actions: list[dict[str, Any]],
) -> str:
    """Join the per-action messages completed so far in this run."""

    return " ".join(
        entry["message"] for entry in completed_actions
    )


def _build_action_success_message(
    action_name: str,
    result_data: dict[str, Any] | None,
) -> str:
    if action_name == "open_cad_document":
        document_name = (
            result_data.get("documentName") if result_data else None
        )

        if document_name:
            return f"{document_name} was opened successfully."

        return "The CATIA document was opened successfully."

    if action_name == "get_active_document":
        document_name = (
            result_data.get("documentName") if result_data else None
        )

        if document_name:
            return f"The active CATIA document is {document_name}."

        return (
            "CATIA does not currently have an active document."
        )

    if action_name == "close_cad_document":
        closed_documents = (
            (result_data.get("closedDocuments") or [])
            if result_data
            else []
        )

        closed_count = len(closed_documents)

        if closed_count == 0:
            return "No CATIA documents needed to be closed."

        if closed_count == 1:
            return f"{closed_documents[0]} was closed successfully."

        return (
            f"{closed_count} CATIA documents were closed "
            "successfully."
        )

    if action_name == "create_cad_document":
        document_name = (
            result_data.get("documentName") if result_data else None
        )

        if document_name:
            return f"{document_name} was created successfully."

        return "A new CATIA document was created successfully."

    return "The CATIA action completed successfully."


# ---------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------

async def plan_action_node(
    state: CatiaAgentState,
) -> dict:
    user_message = state.get(
        "user_message",
        "",
    ).strip()

    if not user_message:
        return {
            "planner_result": InvalidRequestResult(
                status="invalid_request",
                message="The CATIA command cannot be empty.",
            )
        }

    completed_actions = state.get("completed_actions", [])

    if len(completed_actions) >= settings.max_actions_per_run:
        return {
            "planner_result": UnsupportedRequestResult(
                status="unsupported_request",
                message=(
                    "The maximum number of actions for a single "
                    "request was reached."
                ),
            ),
            "sequence_capped": True,
        }

    history = (
        state.get("messages", [])
        + state.get("sequence_progress", [])
    )

    planner_result = await plan_catia_action(
        user_command=user_message,
        catia_context=state.get("catia_context"),
        conversation_history=history,
        completed_actions=completed_actions,
    )

    if isinstance(planner_result, ActionRequiredResult):
        proposed_action = planner_result.action.model_dump()

        for previous in completed_actions:
            if (
                previous["action"] == proposed_action["action"]
                and previous.get("arguments")
                == proposed_action.get("arguments")
            ):
                # Qwen picked an action already completed with the same
                # arguments; treat as sequence-complete instead of looping.
                logger.warning(
                    "planner_duplicate_action_detected",
                    extra={
                        "proposedAction": proposed_action["action"],
                        "completedActionCount": len(completed_actions),
                    },
                )

                planner_result = UnsupportedRequestResult(
                    status="unsupported_request",
                    message=(
                        "No further CATIA actions are required "
                        "for this request."
                    ),
                )
                break

    return {
        "planner_result": planner_result,
    }


def prepare_action_node(
    state: CatiaAgentState,
) -> dict:
    planner_result = state["planner_result"]

    if not isinstance(
        planner_result,
        ActionRequiredResult,
    ):
        raise ValueError(
            "An action could not be prepared because the "
            "planner did not return action_required."
        )

    action_id = str(uuid4())

    pending_action = {
        "status": "action_required",
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "actionId": action_id,
        "action": planner_result.action.model_dump(),
    }

    return {
        "action_id": action_id,
        "pending_action": pending_action,
    }


def await_action_result_node(
    state: CatiaAgentState,
) -> dict:
    """
    Pause the graph and return the pending action to the
    external executor.

    Later, WPF will execute the C# CATIA COM method and send
    the result to the action-results endpoint.
    """

    resumed_value = interrupt(
        state["pending_action"]
    )

    action_result = (
        ActionExecutionResultRequest.model_validate(
            resumed_value
        )
    )

    if action_result.actionId != state["action_id"]:
        raise AgentRunStateConflictError(
            "The returned actionId does not match the "
            "pending CATIA action."
        )

    return {
        "action_result": action_result.model_dump(),
    }


def record_action_result_node(
    state: CatiaAgentState,
) -> dict:
    """
    Record the result of one executed action.

    On failure, the sequence stops immediately and a final response
    is built from everything completed so far plus the failure. On
    success, the result is recorded and the graph loops back to
    plan_action to check whether the original request has more to do.
    """

    action_result = state["action_result"]
    pending_action = state["pending_action"]
    completed_actions = list(state.get("completed_actions", []))

    if not action_result["success"]:
        failure_message = (
            action_result.get("errorMessage")
            or "The CATIA action failed."
        )

        progress = _summarize_completed_actions(completed_actions)
        message = (
            f"{progress} {failure_message}"
            if progress
            else failure_message
        )

        final_response = {
            "status": "execution_failed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": state["action_id"],
            "message": message,
            "errorCode": action_result.get("errorCode"),
        }

        if completed_actions:
            final_response["completedActions"] = completed_actions

        return {
            "final_response": final_response,
            "messages": _record_conversation_turn(state, message),
        }

    action_name = pending_action["action"]["action"]
    action_arguments = pending_action["action"].get("arguments", {})
    result_data = action_result.get("data")
    message = _build_action_success_message(
        action_name, result_data
    )

    completed_actions.append(
        {
            "action": action_name,
            "actionId": state["action_id"],
            "arguments": action_arguments,
            "message": message,
            "data": result_data,
        }
    )

    sequence_progress = list(state.get("sequence_progress", []))
    sequence_progress.append(
        {"role": "assistant", "content": message}
    )

    return {
        "completed_actions": completed_actions,
        "sequence_progress": sequence_progress,
    }


def prepare_sequence_completed_response_node(
    state: CatiaAgentState,
) -> dict:
    """
    Finalize a run once the planner determines nothing from the
    original request remains to be done.
    """

    completed_actions = state.get("completed_actions", [])
    message = _summarize_completed_actions(completed_actions)
    last_action = completed_actions[-1] if completed_actions else None

    final_response = {
        "status": "completed",
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "actionId": (
            last_action["actionId"]
            if last_action
            else state.get("action_id")
        ),
        "message": message,
        "data": last_action["data"] if last_action else None,
        "completedActions": completed_actions,
    }

    return {
        "final_response": final_response,
        "messages": _record_conversation_turn(state, message),
    }


def prepare_terminal_response_node(
    state: CatiaAgentState,
) -> dict:
    """
    Prepare responses for unsupported or invalid requests
    that do not need external execution.
    """
    planner_result = state["planner_result"]
    completed_actions = state.get("completed_actions", [])

    progress = _summarize_completed_actions(completed_actions)
    message = (
        f"{progress} {planner_result.message}"
        if progress
        else planner_result.message
    )

    final_response = {
        "status": planner_result.status,
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "message": message,
    }

    if completed_actions:
        final_response["completedActions"] = completed_actions

    if isinstance(
        planner_result,
        ClarificationRequiredResult,
    ):
        final_response["missingField"] = (
            planner_result.missingField
        )

    return {
        "final_response": final_response,
        "messages": _record_conversation_turn(
            state, final_response["message"]
        ),
    }


# ---------------------------------------------------------
# Conditional routing
# ---------------------------------------------------------

def route_after_planning(
    state: CatiaAgentState,
) -> str:
    planner_result = state["planner_result"] # type: ignore

    if isinstance(
        planner_result,
        ActionRequiredResult,
    ):
        return "prepare_action"

    if (
        isinstance(
            planner_result,
            UnsupportedRequestResult,
        )
        and state.get("completed_actions")
        and not state.get("sequence_capped")
    ):
        return "prepare_sequence_completed_response"

    return "prepare_terminal_response"


def route_after_action_result(
    state: CatiaAgentState,
) -> str:
    if state.get("final_response") is not None:
        return "stop"

    return "continue"


# ---------------------------------------------------------
# Build and compile graph
# ---------------------------------------------------------

graph_builder = StateGraph(CatiaAgentState)

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
    "prepare_completion",
    record_action_result_node,
)

graph_builder.add_node(
    "prepare_sequence_completed_response",
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
        "prepare_sequence_completed_response":
            "prepare_sequence_completed_response",
        "prepare_terminal_response":
            "prepare_terminal_response",
    },
)

graph_builder.add_edge(
    "prepare_action",
    "await_action_result",
)

graph_builder.add_edge(
    "await_action_result",
    "prepare_completion",
)

graph_builder.add_conditional_edges(
    "prepare_completion",
    route_after_action_result,
    {
        "continue": "plan_action",
        "stop": END,
    },
)

graph_builder.add_edge(
    "prepare_sequence_completed_response",
    END,
)

graph_builder.add_edge(
    "prepare_terminal_response",
    END,
)


# ---------------------------------------------------------
# In-memory persistence for the POC
# ---------------------------------------------------------

checkpointer = InMemorySaver()

catia_agent_graph = graph_builder.compile(
    checkpointer=checkpointer
)


# ---------------------------------------------------------
# Run-to-session mapping
# ---------------------------------------------------------

# The LangGraph thread is keyed by session, not run, so the
# action-results endpoint (which only receives a runId) needs this
# lookup to find the right session thread. Process-local only.
_run_sessions: dict[str, str] = {}


def get_session_id_for_run(run_id: str) -> str | None:
    """Return the session that owns this runId, if any."""

    return _run_sessions.get(run_id)


# ---------------------------------------------------------
# Public entry points
# ---------------------------------------------------------

async def start_catia_agent(
    session_id: str,
    user_message: str,
    catia_context: CatiaContext | None = None,
) -> dict[str, Any]:

    config = {
        "configurable": {
            "thread_id": session_id,
        }
    }

    existing_snapshot = catia_agent_graph.get_state(config) # type: ignore

    if existing_snapshot.values and existing_snapshot.next == (
        "await_action_result",
    ):
        raise AgentRunStateConflictError(
            "This session already has a pending CATIA action. "
            "Submit its result before starting a new command."
        )

    run_id = str(uuid4())
    _run_sessions[run_id] = session_id

    result = await catia_agent_graph.ainvoke(
        {
            "session_id": session_id,
            "run_id": run_id,
            "user_message": user_message,
            "catia_context": catia_context,
            # Reset per-run queue state; the thread persists across runs
            # within a session, so stale values from a prior run must
            # not leak into this one.
            "completed_actions": [],
            "sequence_progress": [],
            "sequence_capped": False,
            "final_response": None,
        }, # type: ignore
        config=config, # type: ignore
    )

    interrupts = result.get("__interrupt__")

    if interrupts:
        return interrupts[0].value

    _run_sessions.pop(run_id, None)

    return result["final_response"]


async def resume_catia_agent(
    run_id: str,
    action_result: ActionExecutionResultRequest,
) -> dict[str, Any]:
    session_id = _run_sessions.get(run_id)

    if session_id is None:
        raise KeyError(
            f"No pending Agent run was found for runId: "
            f"{run_id}"
        )

    config = {
        "configurable": {
            "thread_id": session_id,
        }
    }

    snapshot = catia_agent_graph.get_state(config) # type: ignore

    if not snapshot.values:
        raise KeyError(
            f"No pending Agent run was found for runId: "
            f"{run_id}"
        )

    if snapshot.values.get("run_id") != run_id:
        raise AgentRunStateConflictError(
            "This action result does not match the "
            "currently pending Agent run for this session."
        )

    if snapshot.next != (
        "await_action_result",
    ):
        raise AgentRunStateConflictError(
            "This Agent run is not waiting for an "
            "action result."
        )

    result = await catia_agent_graph.ainvoke(
        Command(
            resume=action_result.model_dump()
        ),
        config=config, # type: ignore
    )

    if result.get("__interrupt__"):
        # Another action is queued for this same run; keep the
        # run_id -> session_id mapping alive for its resume call.
        return result["__interrupt__"][0].value

    _run_sessions.pop(run_id, None)

    return result["final_response"]