from pathlib import Path
from typing import Any

import json

import ollama

from catia_agent.models import (
    ActionRequiredResult,
    CatiaContext,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
    CreateCadDocumentAction,
    GetActiveDocumentAction,
    NonAgenticResult,
    OpenCadDocumentAction,
    PlannerResult,
    RequestClarificationAction,
    RouteToGeneralChatAction,
    UnsupportedRequestResult,
)
from catia_agent.mcp_tools import (
    load_ollama_tools_from_mcp,
)


from catia_agent.config import settings
from catia_agent.logging_config import get_logger


logger = get_logger(__name__)

MODEL_NAME = settings.ollama_model

ollama_client = ollama.AsyncClient(
    host=settings.ollama_base_url
)


def validate_open_cad_document(
    action: OpenCadDocumentAction,
) -> None:
    file_path = Path(action.arguments.filePath)

    allowed_extensions = {
        ".catpart",
        ".catproduct",
        ".catdrawing",
    }

    if not file_path.is_absolute():
        raise ValueError(
            "The CATIA document path must be absolute."
        )

    if file_path.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Unsupported CATIA document extension: "
            f"{file_path.suffix or '[missing extension]'}"
        )


def create_typed_action(
    tool_name: str,
    tool_arguments: dict[str, Any],
):
    
    """
    Convert a Qwen-selected MCP tool call into the matching
    typed application action.

    The explicit mapping prevents arbitrary tool names from
    being passed to the future WPF executor.
    """
    
    raw_action = {
        "action": tool_name,
        "arguments": tool_arguments,
    }

    if tool_name == "open_cad_document":
        action = OpenCadDocumentAction.model_validate(
            raw_action
        )

        validate_open_cad_document(action)
        return action

    if tool_name == "get_active_document":
        return GetActiveDocumentAction.model_validate(
            raw_action
        )

    if tool_name == "close_cad_document":
        action = CloseCadDocumentAction.model_validate(
            raw_action
        )

        validate_close_cad_document(action)
        return action

    if tool_name == "create_cad_document":
        return CreateCadDocumentAction.model_validate(
            raw_action
        )

    if tool_name == "request_clarification":
        return RequestClarificationAction.model_validate(
            raw_action
        )

    if tool_name == "route_to_general_chat":
        return RouteToGeneralChatAction.model_validate(
            raw_action
        )

    raise ValueError(
        "The MCP server exposed a tool for which the Agent "
        f"has no approved typed model: {tool_name}"
    )

def build_catia_context_message(
    catia_context: CatiaContext | None,
) -> str:
    if catia_context is None:
        return (
            "CATIA runtime context was not supplied. "
            "Use the available tools whenever current CATIA "
            "state is required."
        )

    if not catia_context.isRunning:
        return (
            "CATIA runtime context:\n"
            "- CATIA is not currently running.\n"
            "- There is no active document.\n"
            "- There are no open documents."
        )

    if catia_context.activeDocument is None:
        active_document = "None"
    else:
        active_document = (
            catia_context.activeDocument.name
        )

    if catia_context.openDocuments:
        open_documents = ", ".join(
            document.name
            for document in catia_context.openDocuments
        )
    else:
        open_documents = "None"

    if catia_context.selectedObjects:
        selected_objects = ", ".join(
            selected_object.name
            for selected_object
            in catia_context.selectedObjects
        )
    else:
        selected_objects = "None"

    return (
        "CATIA runtime context:\n"
        f"- CATIA is running: {catia_context.isRunning}\n"
        f"- Active document: {active_document}\n"
        f"- Open documents: {open_documents}\n"
        f"- Selected objects: {selected_objects}\n\n"
        "This context may be a snapshot. Use an available "
        "CATIA tool when the request requires authoritative "
        "current information."
    )


def validate_close_cad_document(
    action: CloseCadDocumentAction,
) -> None:
    arguments = action.arguments

    if arguments.targetMode == "named":
        if (
            arguments.documentName is None
            or not arguments.documentName.strip()
        ):
            raise ValueError(
                "documentName is required when closing "
                "a named CATIA document."
            )

        arguments.documentName = (
            arguments.documentName.strip()
        )

    elif arguments.documentName is not None:
        arguments.documentName = None


# Meta-tools are handled entirely inside the Agent and are secondary to
# any real CATIA action when Qwen emits parallel tool calls.
_META_TOOL_NAMES = frozenset({
    "route_to_general_chat",
    "request_clarification",
})


def _choose_primary_tool_call(
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pick one tool call, preferring a real CATIA action over meta-tools."""

    for call in tool_calls:
        name = call.get("function", {}).get("name")

        if name and name not in _META_TOOL_NAMES:
            return call

    return tool_calls[0]


def _build_completed_actions_message(
    completed_actions: list[dict[str, Any]] | None,
) -> str | None:
    """Format an explicit anti-repetition note for the planner."""

    if not completed_actions:
        return None

    lines = [
        "The following CATIA actions have already completed successfully "
        "for the current user request. Do NOT call any of them again with "
        "the same arguments. If the user's original request is now fully "
        "satisfied, do not call a tool at all.",
        "",
    ]

    for entry in completed_actions:
        name = entry.get("action", "unknown")
        arguments = entry.get("arguments") or {}

        lines.append(f"- {name} {json.dumps(arguments, sort_keys=True)}")

    return "\n".join(lines)


async def plan_catia_action(
    user_command: str,
    catia_context: CatiaContext | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    completed_actions: list[dict[str, Any]] | None = None,
) -> PlannerResult:
    
    ollama_tools = await load_ollama_tools_from_mcp()
    
    context_message = build_catia_context_message(
        catia_context
    )

    history = conversation_history or []
    trimmed_history = history[
        -settings.max_conversation_history_messages:
    ]

    completed_actions_message = (
        _build_completed_actions_message(completed_actions)
    )
    
    logger.info(
        "qwen_tool_selection_started",
        extra={
            "model": MODEL_NAME,
            "availableToolCount": len(
                ollama_tools
            ),
            "historyMessageCount": len(
                trimmed_history
            ),
            "completedActionCount": len(
                completed_actions or []
            ),
        },
    )

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are the orchestrator for a CATIA engineering "
                "assistant. For every user message, first decide "
                "whether it requires a CATIA action or is a general "
                "conversational query, then respond accordingly. "

                "Call an available tool only when the user's request "
                "requires performing a CATIA action that a tool "
                "supports. Each tool's own description explains "
                "exactly when and how to use it; follow those "
                "descriptions precisely rather than guessing. The "
                "available tools were discovered from the approved "
                "CATIA MCP server. "

                "If the user's message is a greeting, general "
                "question, request for explanation, or anything else "
                "that does not require a CATIA action, call "
                "route_to_general_chat. Do not answer the question "
                "yourself and do not generate a conversational reply. "

                "If the request clearly needs a CATIA action but no "
                "available tool supports it, do not call a tool and "
                "do not call route_to_general_chat either. "

                "When a tool argument identifies a document and the "
                "user refers to it using an ordinal (the first one, "
                "the second one) or a pronoun (it, that document, "
                "that one), resolve the reference using the "
                "conversation history below instead of asking again. "

                "The user's message may request more than one CATIA "
                "action. If the conversation history immediately "
                "below already shows one or more actions completed "
                "for this exact request, determine whether anything "
                "from the original request still remains, and select "
                "the next tool for only the remaining part. If "
                "everything requested has already been completed, do "
                "not call a tool. "

                "Do not invent document names, file paths, or other "
                "arguments that were not stated or resolved from "
                "context. Select exactly one available tool when "
                "possible."
            ),
        },
        {
            "role": "system",
            "content": context_message,
        },
    ]

    if completed_actions_message is not None:
        messages.append(
            {
                "role": "system",
                "content": completed_actions_message,
            }
        )

    messages.extend(trimmed_history)
    messages.append(
        {
            "role": "user",
            "content": user_command,
        }
    )

    response = await ollama_client.chat(
        model=MODEL_NAME,
        messages=messages,
        tools=ollama_tools,
        think=False,
        options={
            "temperature": 0,
        },
    )

    message = response["message"]
    tool_calls = message.get("tool_calls") or []

    if not tool_calls:
        logger.info(
            "qwen_no_matching_tool",
            extra={
                "model": MODEL_NAME,
            },
        )

        return UnsupportedRequestResult(
            status="unsupported_request",
            message=(
                "No available CATIA tool supports this request."
            ),
        )

    if len(tool_calls) > 1:
        # Qwen 3B occasionally emits parallel tool calls on the
        # follow-up planning pass; keep the run alive by choosing one
        # rather than failing the whole request.
        logger.warning(
            "qwen_multiple_tool_calls",
            extra={
                "model": MODEL_NAME,
                "toolCallCount": len(tool_calls),
                "toolNames": [
                    call.get("function", {}).get("name")
                    for call in tool_calls
                ],
            },
        )

    chosen_call = _choose_primary_tool_call(tool_calls)

    function = chosen_call["function"]

    tool_name = function["name"]
    tool_arguments = function.get("arguments") or {}

    logger.info(
        "qwen_tool_selected",
        extra={
            "model": MODEL_NAME,
            "selectedTool": tool_name,
        },
    )
    
    typed_action = create_typed_action(
        tool_name=tool_name,
        tool_arguments=tool_arguments,
    )

    if isinstance(
        typed_action,
        RequestClarificationAction,
    ):
        return ClarificationRequiredResult(
            status="clarification_required",
            message=typed_action.arguments.question,
            missingField=(
                typed_action.arguments.missingField
            ),
        )

    if isinstance(
        typed_action,
        RouteToGeneralChatAction,
    ):
        logger.info(
            "orchestrator_routed_to_general_chat",
            extra={
                "model": MODEL_NAME,
            },
        )

        return NonAgenticResult(
            status="non_agentic",
            message=(
                "This request does not require a CATIA action. "
                "Route it to the general chat service."
            ),
        )

    return ActionRequiredResult(
        status="action_required",
        action=typed_action,
    )