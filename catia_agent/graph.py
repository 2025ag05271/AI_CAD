from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    CatiaContext,
    InvalidRequestResult,
    PlannerResult,
    ClarificationRequiredResult
)
from catia_agent.planner import plan_catia_action


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

    planner_result = await plan_catia_action(
        user_command=user_message,
        catia_context=state.get("catia_context"),
        conversation_history=state.get("messages", []),
    )

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
        raise ValueError(
            "The returned actionId does not match the "
            "pending CATIA action."
        )

    return {
        "action_result": action_result.model_dump(),
    }


def prepare_completion_node(
    state: CatiaAgentState,
) -> dict:
    action_result = state["action_result"]
    pending_action = state["pending_action"]

    if not action_result["success"]:
        final_response = {
            "status": "execution_failed",
            "sessionId": state["session_id"],
            "runId": state["run_id"],
            "actionId": state["action_id"],
            "message": (
                action_result.get("errorMessage")
                or "The CATIA action failed."
            ),
            "errorCode": action_result.get("errorCode"),
        }

        return {
            "final_response": final_response,
            "messages": _record_conversation_turn(
                state, final_response["message"]
            ),
        }

    action_name = pending_action["action"]["action"]
    result_data = action_result.get("data")

    if action_name == "open_cad_document":
        document_name = None

        if result_data:
            document_name = result_data.get(
                "documentName"
            )

        if document_name:
            message = (
                f"{document_name} was opened successfully."
            )
        else:
            message = (
                "The CATIA document was opened successfully."
            )

    elif action_name == "get_active_document":
        document_name = None

        if result_data:
            document_name = result_data.get(
                "documentName"
            )

        if document_name:
            message = (
                f"The active CATIA document is "
                f"{document_name}."
            )
        else:
            message = (
                "CATIA does not currently have an "
                "active document."
            )

    elif action_name == "close_cad_document":
        closed_documents = []

        if result_data:
            closed_documents = (
                result_data.get("closedDocuments")
                or []
            )

        closed_count = len(closed_documents)

        if closed_count == 0:
            message = (
                "No CATIA documents needed to be closed."
            )

        elif closed_count == 1:
            message = (
                f"{closed_documents[0]} was closed "
                "successfully."
            )

        else:
            message = (
                f"{closed_count} CATIA documents were "
                "closed successfully."
            )
    
    else:
        message = (
            "The CATIA action completed successfully."
        )

    final_response = {
        "status": "completed",
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "actionId": state["action_id"],
        "message": message,
        "data": result_data,
    }

    return {
        "final_response": final_response,
        "messages": _record_conversation_turn(
            state, final_response["message"]
        ),
    }


def prepare_terminal_response_node(
    state: CatiaAgentState,
) -> dict:
    """
    Prepare responses for unsupported or invalid requests
    that do not need external execution.
    """
    planner_result = state["planner_result"]

    final_response = {
        "status": planner_result.status,
        "sessionId": state["session_id"],
        "runId": state["run_id"],
        "message": planner_result.message,
    }

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

    return "prepare_terminal_response"


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
    prepare_completion_node,
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

graph_builder.add_edge(
    "prepare_completion",
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
        raise ValueError(
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
        raise ValueError(
            "This action result does not match the "
            "currently pending Agent run for this session."
        )

    if snapshot.next != (
        "await_action_result",
    ):
        raise ValueError(
            "This Agent run is not waiting for an "
            "action result."
        )

    result = await catia_agent_graph.ainvoke(
        Command(
            resume=action_result.model_dump()
        ),
        config=config, # type: ignore
    )

    _run_sessions.pop(run_id, None)

    if result.get("__interrupt__"):
        return result["__interrupt__"][0].value

    return result["final_response"]