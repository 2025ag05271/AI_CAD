from __future__ import annotations

import json
import time
from pathlib import PureWindowsPath
from typing import Any

from pydantic import ValidationError

from catia_agent.config import settings
from catia_agent.logging_config import get_logger
from catia_agent.mcp_tools import (
    load_ollama_tools_from_mcp,
)
from catia_agent.model_providers import (
    MODEL_NAME,
    ModelProviderError,
    ModelProviderTimeoutError,
    ModelResponseFormatError,
    ToolCallingModelClient,
    model_client,
)
from catia_agent.models import (
    ActionRequiredResult,
    CatiaContext,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CompleteRequestAction,
    CreateCadDocumentAction,
    DownloadCadAction,
    GetActiveDocumentAction,
    InvalidRequestResult,
    NonAgenticResult,
    OpenCadDocumentAction,
    PlannerResult,
    PlanningErrorResult,
    ReportUnsupportedCatiaAction,
    RequestClarificationAction,
    RequestCompletedResult,
    RouteToGeneralChatAction,
    StartModelAction,
    UnsupportedRequestResult,
)


logger = get_logger(__name__)

__all__ = [
    "MODEL_NAME",
    "ModelProviderError",
    "ModelProviderTimeoutError",
    "ModelResponseFormatError",
    "ToolCallingModelClient",
    "model_client",
]


# -------------------------------------------------------------------------
# Orchestrator-only tools
#
# These are planning control signals. They are not CATIA executor tools
# and are therefore not published by the CATIA MCP server.
# -------------------------------------------------------------------------

ORCHESTRATOR_TOOLS: list[
    dict[str, Any]
] = [
    {
        "type": "function",
        "function": {
            "name": "request_clarification",
            "description": (
                "Ask the user for information required before a "
                "supported CATIA action can be prepared. Use this "
                "only when the intended CATIA operation is supported "
                "but a required argument or reference is missing or "
                "ambiguous."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "A concise question asking for the "
                            "missing information."
                        ),
                    },
                    "missingField": {
                        "type": "string",
                        "description": (
                            "Stable name of the missing or "
                            "ambiguous field."
                        ),
                    },
                },
                "required": [
                    "question",
                    "missingField",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_general_chat",
            "description": (
                "Route a message that does not require executing a "
                "CATIA action to the general chat service. Use this "
                "for greetings, explanations, informational "
                "questions, capability questions, and requests "
                "unrelated to CATIA execution. Do not answer the "
                "message yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": (
                "report_unsupported_catia_action"
            ),
            "description": (
                "Report that the user requested a CATIA operation "
                "but no available approved CATIA tool can perform "
                "it. Use this only for execution requests, not for "
                "informational questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requestedCapability": {
                        "type": "string",
                        "description": (
                            "Short description of the CATIA "
                            "capability requested by the user."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "enum": [
                            "tool_not_available",
                            "operation_not_allowed",
                            "executor_not_available",
                        ],
                    },
                },
                "required": [
                    "requestedCapability",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
    "type": "function",
    "function": {
        "name": "complete_request",
        "description": (
            "Signal that all CATIA actions requested in the "
            "original user message have completed successfully "
            "and no further action remains. Use this only during "
            "follow-up planning after one or more actions have "
            "already completed."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            },
        },
    },
]


ORCHESTRATOR_TOOL_NAMES = frozenset(
    {
        "request_clarification",
        "route_to_general_chat",
        "report_unsupported_catia_action",
        "complete_request",
    }
)

APPROVED_CATIA_TOOL_NAMES = frozenset(
    {
        "open_cad_document",
        "close_cad_document",
        "create_cad_document",
        "download_cad",
        "start_model",
    }
)

IC_START_MODEL_ALIASES = (
    " ic ",
    " inflatable curtain ",
    " curtain airbag ",
)


def _is_unsupported_ic_start_model_request(
    user_command: str,
) -> bool:
    normalized_command = (
        " "
        + " ".join(
            user_command.casefold().split()
        )
        + " "
    )

    has_model_intent = (
        "model" in normalized_command
        or "start" in normalized_command
        or "create" in normalized_command
    )

    if not has_model_intent:
        return False

    return any(
        alias in normalized_command
        for alias in IC_START_MODEL_ALIASES
    )


# -------------------------------------------------------------------------
# Generic response helpers
# -------------------------------------------------------------------------

def _get_value(
    source: Any,
    key: str,
    default: Any = None,
) -> Any:
    if isinstance(source, dict):
        return source.get(
            key,
            default,
        )

    return getattr(
        source,
        key,
        default,
    )


def _extract_tool_calls(
    response: Any,
) -> list:
    
    message = _get_value(
        response,
        "message",
    )

    if message is None:
        raise ModelResponseFormatError(
            "The model response did not contain a message."
        )

    tool_calls = _get_value(
        message,
        "tool_calls",
        [],
    )

    if tool_calls is None:
        return []

    if not isinstance(tool_calls, list):
        try:
            return list(tool_calls)
        except TypeError as exc:
            raise ModelResponseFormatError(
                "The model returned an invalid tool-call list."
            ) from exc

    return tool_calls


def _extract_tool_call(
    tool_call: Any,
) -> tuple[str, dict[str, Any]]:
    function = _get_value(
        tool_call,
        "function",
    )

    if function is None:
        raise ModelResponseFormatError(
            "The model returned a tool call without a function."
        )

    tool_name = _get_value(
        function,
        "name",
    )

    if not isinstance(tool_name, str):
        raise ModelResponseFormatError(
            "The model returned a tool call without a valid name."
        )

    tool_name = tool_name.strip()

    if not tool_name:
        raise ModelResponseFormatError(
            "The model returned an empty tool name."
        )

    arguments = _get_value(
        function,
        "arguments",
        {},
    )

    if arguments is None:
        arguments = {}

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ModelResponseFormatError(
                f"Tool '{tool_name}' returned invalid JSON "
                "arguments."
            ) from exc

    if not isinstance(arguments, dict):
        raise ModelResponseFormatError(
            f"Tool '{tool_name}' returned arguments that are "
            "not a JSON object."
        )

    return tool_name, arguments


def _safe_tool_call_names(
    tool_calls: list[Any],
) -> list[str]:
    """
    Best-effort tool-name extraction for diagnostic logging only.

    Never raises: a malformed entry is reported as "unreadable" rather
    than interrupting the (already-erroring) multi-tool-call log line.
    """

    names: list[str] = []

    for tool_call in tool_calls:
        try:
            tool_name, _ = _extract_tool_call(tool_call)
            names.append(tool_name)

        except ModelResponseFormatError:
            names.append("unreadable")

    return names


# -------------------------------------------------------------------------
# Deterministic action validation
# -------------------------------------------------------------------------

def validate_open_cad_document(
    action: OpenCadDocumentAction,
) -> None:
    path = PureWindowsPath(
        action.arguments.filePath
    )

    allowed_extensions = {
        ".catpart",
        ".catproduct",
        ".catdrawing",
    }

    if not path.is_absolute():
        raise ValueError(
            "The CATIA document path must be an absolute "
            "Windows path."
        )

    if path.suffix.lower() not in allowed_extensions:
        displayed_extension = (
            path.suffix
            if path.suffix
            else "[missing extension]"
        )

        raise ValueError(
            "Unsupported CATIA document extension: "
            f"{displayed_extension}"
        )


def validate_close_cad_document(
    action: CloseCadDocumentAction,
) -> None:
    target_mode = action.arguments.targetMode
    document_name = action.arguments.documentName

    if target_mode == "named":
        if document_name is None:
            raise ValueError(
                "documentName is required when targetMode "
                "is named."
            )

        normalized_name = document_name.strip()

        if not normalized_name:
            raise ValueError(
                "documentName is required when targetMode "
                "is named."
            )

        object.__setattr__(
            action.arguments,
            "documentName",
            normalized_name,
        )
        return

    object.__setattr__(
        action.arguments,
        "documentName",
        None,
    )


def create_typed_action(
    tool_name: str,
    tool_arguments: dict[str, Any],
) -> (
    OpenCadDocumentAction
    | GetActiveDocumentAction
    | CloseCadDocumentAction
    | CreateCadDocumentAction
    | DownloadCadAction
    | StartModelAction
    | RequestClarificationAction
    | RouteToGeneralChatAction
    | ReportUnsupportedCatiaAction
    | CompleteRequestAction
):
    """
    Convert a model-selected tool call into an explicitly approved,
    typed action.

    MCP discovery does not automatically authorize execution.
    """

    raw_action = {
        "action": tool_name,
        "arguments": tool_arguments,
    }
    
    logger.info(
        "planner_tool_arguments",
        extra={
            "tool": tool_name,
            "arguments": tool_arguments
        }
    )

    if tool_name == "open_cad_document":
        action = (
            OpenCadDocumentAction.model_validate(
                raw_action
            )
        )

        validate_open_cad_document(action)
        return action

    if tool_name == "get_active_document":
        return (
            GetActiveDocumentAction.model_validate(
                raw_action
            )
        )

    if tool_name == "close_cad_document":
        action = (
            CloseCadDocumentAction.model_validate(
                raw_action
            )
        )

        validate_close_cad_document(action)
        return action

    if tool_name == "create_cad_document":
        return (
            CreateCadDocumentAction.model_validate(
                raw_action
            )
        )

    if tool_name == "download_cad":
        return (
            DownloadCadAction.model_validate(
                raw_action
            )
        )

    if tool_name == "start_model":
        return (
            StartModelAction.model_validate(
                raw_action
            )
        )

    if tool_name == "request_clarification":
        return (
            RequestClarificationAction.model_validate(
                raw_action
            )
        )

    if tool_name == "route_to_general_chat":
        return (
            RouteToGeneralChatAction.model_validate(
                raw_action
            )
        )

    if (
        tool_name
        == "report_unsupported_catia_action"
    ):
        return (
            ReportUnsupportedCatiaAction.model_validate(
                raw_action
            )
        )

    if tool_name == "complete_request":
        return CompleteRequestAction.model_validate(
            raw_action
        )
        
    raise ValueError(
        "The model selected an unapproved tool: "
        f"{tool_name}"
    )


# -------------------------------------------------------------------------
# Prompt context
# -------------------------------------------------------------------------

def build_catia_context_message(
    catia_context: CatiaContext | None,
) -> str:
    if catia_context is None:
        return (
            "CATIA runtime context was not supplied. Treat current "
            "CATIA state as unknown. Use an available CATIA tool "
            "when authoritative current state is required."
        )

    if not catia_context.isRunning:
        return (
            "CATIA runtime context:\n"
            "- CATIA is not currently running.\n"
            "- There is no active document.\n"
            "- There are no open documents.\n"
            "- There are no selected objects."
        )

    active_document = (
        catia_context.activeDocument.name
        if catia_context.activeDocument
        else "None"
    )

    open_documents = (
        ", ".join(
            document.name
            for document
            in catia_context.openDocuments
        )
        if catia_context.openDocuments
        else "None"
    )

    selected_objects = (
        ", ".join(
            selected_object.name
            for selected_object
            in catia_context.selectedObjects
        )
        if catia_context.selectedObjects
        else "None"
    )

    context_version = (
        str(catia_context.contextVersion)
        if catia_context.contextVersion
        is not None
        else "Not supplied"
    )

    return (
        "CATIA runtime context snapshot:\n"
        f"- CATIA is running: {catia_context.isRunning}\n"
        f"- Active document: {active_document}\n"
        f"- Open documents: {open_documents}\n"
        f"- Selected objects: {selected_objects}\n"
        f"- Context version: {context_version}\n\n"
        "This context is a snapshot and may be stale. Use a CATIA "
        "tool whenever the request requires authoritative current "
        "state."
    )


def _build_completed_actions_message(
    user_command: str,
    completed_actions: (
        list[dict[str, Any]] | None
    ),
) -> str | None:
    if not completed_actions:
        return None

    lines = [
        "Goal-delta analysis for this planning step.",
        "",
        f'Original user request: "{user_command}"',
        "",
        "Actions already completed in this run:",
    ]

    for entry in completed_actions:
        action_name = entry.get(
            "action",
            "unknown",
        )

        arguments = entry.get(
            "arguments",
            {},
        )

        lines.append(
            f"- {action_name} "
            f"{json.dumps(arguments, sort_keys=True)}"
        )

    lines.extend(
        [
            "",
            "Compare the original user request against the actions "
            "already completed and decide what is left.",
            "",
            "If one or more CATIA operations from the original "
            "request are still not done, select the SINGLE NEXT "
            "remaining CATIA action.",
            "",
            "If every CATIA operation in the original request is "
            "done, call complete_request.",
            "",
            "Do not repeat any action listed above with the same "
            "name and the same arguments. Do not invent additional "
            "work that was not in the original request.",
        ]
    )

    return "\n".join(lines)


def _build_system_prompt() -> str:
    return (
        "You are the routing and tool-selection orchestrator for a "
        "CATIA engineering assistant.\n\n"
        "You must select exactly one available tool for every user "
        "message. Never answer the user directly and never return "
        "free-form text instead of a tool call. A tool call is "
        "mandatory on every turn: there is no option to respond with "
        "no tool call, so when you are not confident that a CATIA "
        "operation is being requested right now, select "
        "route_to_general_chat rather than guessing a CATIA tool. "
        "Selecting a CATIA execution tool for a conversational message "
        "is a worse outcome than routing it to chat, because it can "
        "trigger a real CATIA action the user did not ask for.\n\n"
        "You must return exactly one tool call in this response, even "
        "if you can see that more than one CATIA action will "
        "eventually be needed. Select only the single next action now; "
        "you will be asked again after it completes (see the "
        "goal-delta guidance below for follow-up turns).\n\n"
        "Step 1: decide whether this message requires executing a "
        "CATIA operation (agentic) or not (chat). Step 2: if agentic, "
        "select the single CATIA tool for the next remaining action. "
        "If not agentic, select the correct orchestrator tool.\n\n"
        "Select a CATIA MCP tool only when the user is asking to "
        "perform an operation supported by that tool.\n\n"
        "Select route_to_general_chat for greetings, explanations, "
        "informational questions, capability questions, hypothetical "
        "questions, opinions, ambiguous messages, and any request that "
        "does not clearly require executing a CATIA operation. This is "
        "the default, safe choice whenever you are unsure.\n\n"
        "Select request_clarification when the intended CATIA action "
        "is supported but required information is missing or "
        "ambiguous.\n\n"
        "Select report_unsupported_catia_action when the user clearly "
        "requests execution of a CATIA operation but no approved "
        "CATIA tool can perform it.\n\n"
        "Distinguish questions from commands using the verb mood, not "
        "just keyword overlap with a tool name. Examples:\n"
        "- 'How do I close a document?' -> informational -> "
        "route_to_general_chat.\n"
        "- 'What does open_cad_document do?' -> informational -> "
        "route_to_general_chat.\n"
        "- 'Close the active document.' -> execution -> "
        "close_cad_document.\n"
        "- 'Can you open Gearbox.CATProduct for me?' -> a polite "
        "request phrased as a question is still execution -> "
        "open_cad_document.\n\n"
        "A message can mention a CATIA capability without asking to "
        "run it now (for example, describing a future plan, asking "
        "for advice, or asking whether something is possible). Only "
        "select a CATIA tool when the user wants that operation "
        "performed as part of this turn.\n\n"
        "Do not invent document names, paths, selections, or other "
        "arguments. Resolve pronouns and ordinal references only from "
        "trusted CATIA context and conversation history. If a message "
        "mixes a question and a command (for example, 'What is open "
        "right now, and can you also close it?'), resolve only the "
        "execution part on this turn and select the CATIA tool for "
        "it; the question part is not answered by you.\n\n"
        "For compound requests, select only the next remaining CATIA "
        "action. Actions already completed for the current request "
        "must not be repeated.\n\n"
        "Select complete_request only during follow-up planning when "
        "one or more CATIA actions have completed and the original "
        "user request is now fully satisfied.\n\n"
    )


def _build_followup_system_prompt() -> str:
    """
    Leaner prompt used once at least one action has already completed
    in this run.

    The ask-vs-agent decision and the question-vs-command examples in
    _build_system_prompt only matter for the first planning turn; by
    follow-up time this request is already known to be agentic. The
    goal-delta comparison itself (what remains, complete_request) is
    covered by the per-turn system message built by
    _build_completed_actions_message, so it is not repeated here.
    """

    return (
        "You are continuing to plan CATIA actions for a request that "
        "already has one or more completed steps in this run. Use the "
        "goal-delta analysis below to decide what remains.\n\n"
        "You must select exactly one available tool. Never answer the "
        "user directly and never return free-form text instead of a "
        "tool call.\n\n"
        "Select request_clarification if the next remaining CATIA "
        "action is missing required information. Select "
        "report_unsupported_catia_action if the next remaining action "
        "has no approved CATIA tool. Select route_to_general_chat only "
        "if no CATIA action remains and the only thing left from the "
        "original request is informational.\n\n"
        "Do not invent document names, paths, selections, or other "
        "arguments. Resolve pronouns and ordinal references only from "
        "trusted CATIA context and conversation history.\n\n"
    )


# -------------------------------------------------------------------------
# Planner
# -------------------------------------------------------------------------

async def plan_catia_action(
    user_command: str,
    catia_context: CatiaContext | None = None,
    conversation_history: (
        list[dict[str, str]] | None
    ) = None,
    completed_actions: (
        list[dict[str, Any]] | None
    ) = None,
    retry_hint: str | None = None,
    planning_mode: str | None = None,
    planning_mode_confidence: str | None = None,
) -> PlannerResult:
    normalized_command = user_command.strip()

    if not normalized_command:
        return InvalidRequestResult(
            status="invalid_request",
            message="The user message must not be empty.",
        )

    if _is_unsupported_ic_start_model_request(
        normalized_command
    ):
        return UnsupportedRequestResult(
            status="unsupported_request",
            message="Currently IC start model is not supported.",
            requestedCapability="start_model_ic",
            reason="operation_not_allowed",
        )

    mcp_tools = await load_ollama_tools_from_mcp()

    available_tool_names = {
        tool["function"]["name"]
        for tool in mcp_tools
    }

    unexpected_tools = (
        available_tool_names
        - APPROVED_CATIA_TOOL_NAMES
    )

    if unexpected_tools:
        raise ValueError(
            "The MCP server exposed CATIA tools that are not "
            "integrated by the Agent: "
            + ", ".join(
                sorted(unexpected_tools)
            )
        )

    missing_tools = (
        APPROVED_CATIA_TOOL_NAMES
        - available_tool_names
    )

    if missing_tools:
        logger.warning(
            "mcp_approved_tools_missing",
            extra={
                "missingToolNames": sorted(
                    missing_tools
                ),
            },
        )

    available_tools = [
        *mcp_tools,
        *ORCHESTRATOR_TOOLS,
    ]

    history = conversation_history or []

    trimmed_history = history[
        -settings.max_conversation_history_messages:
    ]

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                _build_followup_system_prompt()
                if completed_actions
                else _build_system_prompt()
            ),
        },
        {
            "role": "system",
            "content": build_catia_context_message(
                catia_context
            ),
        },
    ]

    completed_actions_message = (
        _build_completed_actions_message(
            normalized_command,
            completed_actions,
        )
    )

    if completed_actions_message:
        messages.append(
            {
                "role": "system",
                "content": (
                    completed_actions_message
                ),
            }
        )

    messages.extend(trimmed_history)

    if retry_hint:
        messages.append(
            {
                "role": "system",
                "content": retry_hint,
            }
        )

    messages.append(
        {
            "role": "user",
            "content": normalized_command,
        }
    )

    logger.info(
        "model_tool_selection_started",
        extra={
            "model": MODEL_NAME,
            "availableToolCount": len(
                available_tools
            ),
            "historyMessageCount": len(
                trimmed_history
            ),
            "completedActionCount": len(
                completed_actions or []
            ),
        },
    )

    started_at = time.perf_counter()

    response = await model_client.select_tool(
        messages=messages,
        tools=available_tools,
    )

    duration_ms = round(
        (
            time.perf_counter()
            - started_at
        )
        * 1000,
        2,
    )

    tool_calls = _extract_tool_calls(response)

    if not tool_calls:
        if completed_actions:
            if (
                planning_mode
                == "follow_up_required"
                and planning_mode_confidence
                == "high"
            ):
                logger.warning(
                    "planner_no_decision_blocked_followup_completion",
                    extra={
                        "model": MODEL_NAME,
                        "durationMs": duration_ms,
                        "completedActionCount": len(
                            completed_actions
                        ),
                        "planningMode": planning_mode,
                        "planningModeConfidence": (
                            planning_mode_confidence
                        ),
                    },
                )

                return PlanningErrorResult(
                    status="planning_error",
                    errorCode=(
                        "MODEL_RETURNED_NO_DECISION"
                    ),
                    message=(
                        "The Agent could not determine the next "
                        "required CATIA action for this request."
                    ),
                )

            logger.info(
                "planner_no_decision_treated_as_complete",
                extra={
                    "model": MODEL_NAME,
                    "durationMs": duration_ms,
                    "completedActionCount": len(
                        completed_actions
                    ),
                },
            )

            return RequestCompletedResult(
                status="request_completed",
            )

        logger.error(
            "model_returned_no_decision",
            extra={
                "model": MODEL_NAME,
                "durationMs": duration_ms,
            },
        )

        return PlanningErrorResult(
            status="planning_error",
            errorCode=(
                "MODEL_RETURNED_NO_DECISION"
            ),
            message=(
                "The Agent could not produce a structured "
                "routing decision."
            ),
        )

    if len(tool_calls) != 1:
        # Qwen 3.6 27B natively supports multi-tool-call completions and
        # may emit more than one call more often than the earlier small
        # model did. This is intentionally rejected rather than queued:
        # each CATIA action requires a real round trip to the .NET
        # executor (action_required -> await_action_result ->
        # record_action_result) before the next action can be safely
        # chosen, since a later action may depend on an earlier one's
        # result. Accepting multiple calls per turn would require
        # executing them out of order or guessing which one to run
        # first, so the deterministic sequential loop in graph.py stays
        # the source of truth for multi-action requests (see README
        # Section 8.2), and this path only logs and reports a planning
        # error for monitoring how often it happens.
        logger.error(
            "model_returned_multiple_decisions",
            extra={
                "model": MODEL_NAME,
                "toolCallCount": len(
                    tool_calls
                ),
                "selectedTools": _safe_tool_call_names(
                    tool_calls
                ),
                "durationMs": duration_ms,
            },
        )

        return PlanningErrorResult(
            status="planning_error",
            errorCode=(
                "MODEL_RETURNED_MULTIPLE_DECISIONS"
            ),
            message=(
                "The Agent returned multiple routing decisions "
                "for one planning step."
            ),
        )

    try:
        (
            tool_name,
            tool_arguments,
        ) = _extract_tool_call(
            tool_calls[0]
        )

    except ModelResponseFormatError:
        logger.exception(
            "model_tool_call_format_invalid",
            extra={
                "model": MODEL_NAME,
                "durationMs": duration_ms,
            },
        )

        return PlanningErrorResult(
            status="planning_error",
            errorCode=(
                "MODEL_RETURNED_INVALID_ARGUMENTS"
            ),
            message=(
                "The Agent returned an invalid structured "
                "tool call."
            ),
        )

    if (
        tool_name not in available_tool_names
        and tool_name
        not in ORCHESTRATOR_TOOL_NAMES
    ):
        logger.error(
            "model_selected_unknown_tool",
            extra={
                "model": MODEL_NAME,
                "selectedTool": tool_name,
                "durationMs": duration_ms,
            },
        )

        return PlanningErrorResult(
            status="planning_error",
            errorCode=(
                "MODEL_RETURNED_INVALID_TOOL"
            ),
            message=(
                "The Agent selected a tool that is not "
                "available."
            ),
        )

    logger.info(
        "model_tool_selected",
        extra={
            "model": MODEL_NAME,
            "selectedTool": tool_name,
            "durationMs": duration_ms,
        },
    )

    try:
        typed_action = create_typed_action(
            tool_name=tool_name,
            tool_arguments=tool_arguments,
        )

    except ValidationError:
        logger.exception(
            "model_tool_arguments_invalid",
            extra={
                "model": MODEL_NAME,
                "selectedTool": tool_name,
            },
        )
        raise

    if isinstance(
        typed_action,
        RequestClarificationAction,
    ):
        return ClarificationRequiredResult(
            status="clarification_required",
            message=(
                typed_action.arguments.question
            ),
            missingField=(
                typed_action.arguments.missingField
            ),
        )

    if isinstance(
        typed_action,
        RouteToGeneralChatAction,
    ):
        return NonAgenticResult(
            status="non_agentic",
            message=(
                "This request does not require a CATIA "
                "execution action."
            ),
            routeTarget="general_chat",
            routeReason="general_conversation",
        )

    if isinstance(
    typed_action,
    CompleteRequestAction,
    ):
        if not completed_actions:
            return PlanningErrorResult(
                status="planning_error",
                errorCode="MODEL_RETURNED_INVALID_TOOL",
                message=(
                    "The Agent attempted to complete a request "
                    "before any CATIA action had executed."
                ),
            )

        return RequestCompletedResult(
            status="request_completed",
        )

    if isinstance(
        typed_action,
        ReportUnsupportedCatiaAction,
    ):
        return UnsupportedRequestResult(
            status="unsupported_request",
            message=(
                "No available CATIA tool supports this request."
            ),
            requestedCapability=(
                typed_action.arguments.requestedCapability
            ),
            reason=typed_action.arguments.reason,
        )

    if isinstance(
        typed_action,
        (
            OpenCadDocumentAction,
            GetActiveDocumentAction,
            CloseCadDocumentAction,
            CreateCadDocumentAction,
            DownloadCadAction,
            StartModelAction,
        ),
    ):
        return ActionRequiredResult(
            status="action_required",
            action=typed_action,
        )

    return PlanningErrorResult(
        status="planning_error",
        errorCode="MODEL_RETURNED_INVALID_TOOL",
        message=(
            "The Agent produced an action type that could not "
            "be routed."
        ),
    )