from __future__ import annotations

import json
import time
from typing import Any

from langgraph.types import interrupt

from catia_agent.config import settings
from catia_agent.graph_responses import _new_identifier
from catia_agent.graph_state import (
    AgentRunStateConflictError,
    CatiaAgentState,
)
from catia_agent.logging_config import get_logger, log_duration
from catia_agent.models import (
    ActionExecutionResultRequest,
    ActionRequiredResult,
    NonAgenticResult,
    PlannerResult,
    RequestCompletedResult,
    UnsupportedRequestResult,
)
from catia_agent.planner import plan_catia_action


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Graph nodes: planning and action preparation
# -------------------------------------------------------------------------

async def plan_action_node(
    state: CatiaAgentState,
) -> dict[str, Any]:
    with log_duration(
        logger,
        "plan_action_node",
        completedActionCount=len(
            state.get("completed_actions", [])
        ),
    ) as timing:
        update = await _plan_action(state)
        timing["plannerResult"] = type(
            update["planner_result"]
        ).__name__
        return update


async def _plan_action(
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

    # Embedding classifier already decided this run's opening message
    # is not a CATIA action request: skip the model call entirely and
    # route straight to general chat, same outcome as the model
    # selecting route_to_general_chat itself. Only applies before the
    # first action; a False decision from an earlier turn never
    # applies here because completed_actions would be non-empty.
    if (
        not completed_actions
        and state.get("is_agentic_request") is False
    ):
        logger.info(
            "embedding_classifier_routed_to_general_chat",
            extra={"runId": state["run_id"]},
        )

        return {
            "planner_result": NonAgenticResult(
                status="non_agentic",
                message=(
                    "This request does not require a CATIA "
                    "execution action."
                ),
                routeTarget="general_chat",
                routeReason="general_conversation",
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
        "action_requested_at": time.time(),
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
