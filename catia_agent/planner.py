from pathlib import Path
from typing import Any

import ollama

from catia_agent.models import (
    ActionRequiredResult,
    CatiaContext,
    ClarificationRequiredResult,
    CloseCadDocumentAction,
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


async def plan_catia_action(
    user_command: str,
    catia_context: CatiaContext | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> PlannerResult:
    
    ollama_tools = await load_ollama_tools_from_mcp()
    
    context_message = build_catia_context_message(
        catia_context
    )

    history = conversation_history or []
    trimmed_history = history[
        -settings.max_conversation_history_messages:
    ]
    
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
        },
    )
    
    response = await ollama_client.chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the orchestrator for a CATIA engineering "
                    "assistant. For every user message, first decide "
                    "whether it requires a CATIA action or is a general "
                    "conversational query, then respond accordingly. "

                    "Call an available tool only when the user's request "
                    "requires performing a CATIA action that a tool "
                    "supports. The available tools were discovered from "
                    "the approved CATIA MCP server. "

                    "If the user's message is a greeting, general "
                    "question, request for explanation, or anything else "
                    "that does not require a CATIA action, call "
                    "route_to_general_chat. Do not answer the question "
                    "yourself and do not generate a conversational reply. "

                    "If the request clearly needs a CATIA action but no "
                    "available tool supports it, do not call a tool and "
                    "do not call route_to_general_chat either. "

                    "For close-document requests, follow these rules: "
                    "Use close_cad_document with targetMode='active' "
                    "when the user says this document, current document, "
                    "active document, or currently focused document. "

                    "Use targetMode='named' when the user explicitly "
                    "provides a document name. Preserve that name exactly "
                    "in documentName. "

                    "Use targetMode='all' only when the user explicitly "
                    "asks to close all documents. "

                    "If the user refers to a previously mentioned document "
                    "using an ordinal (the first one, the second one) or a "
                    "pronoun (it, that document, that one), resolve the "
                    "reference using the conversation history below and use "
                    "targetMode='named' with the exact resolved document "
                    "name. "

                    "If the user says only close document, close a "
                    "document, or otherwise fails to identify the target "
                    "and it cannot be resolved from history, use "
                    "request_clarification and ask which document "
                    "should be closed. "

                    "If the user says close the document which is open "
                    "and the CATIA context shows exactly one open document, "
                    "use targetMode='active'. If multiple documents are "
                    "open, request clarification. "

                    "Do not invent document names. "
                    "Do not treat all as active or named. "
                    "Select exactly one available tool when possible."
                ),
            },
            {
                "role": "system",
                "content": context_message,
            },
            *trimmed_history,
            {
                "role": "user",
                "content": user_command,
            },
        ],
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

    if len(tool_calls) != 1:
        raise ValueError(
            "Expected exactly one CATIA tool call, but "
            f"Qwen returned {len(tool_calls)}."
        )

    function = tool_calls[0]["function"]

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