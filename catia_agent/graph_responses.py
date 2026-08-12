from __future__ import annotations

from typing import Any
from uuid import uuid4

from catia_agent.config import settings
from catia_agent.graph_state import CatiaAgentState
from catia_agent.models import ActionExecutionResultRequest


# -------------------------------------------------------------------------
# Response helpers
# -------------------------------------------------------------------------

def _new_identifier() -> str:
    return str(uuid4())


def _serialize_completed_actions(
    completed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(action)
        for action in completed_actions
    ]


def _completed_actions_or_none(
    completed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if not completed_actions:
        return None

    return _serialize_completed_actions(
        completed_actions
    )


def _join_completed_messages(
    completed_actions: list[dict[str, Any]],
) -> str:
    messages = [
        str(action.get("message", "")).strip()
        for action in completed_actions
        if str(action.get("message", "")).strip()
    ]

    if not messages:
        return (
            "The requested CATIA actions completed "
            "successfully."
        )

    return " ".join(messages)


def _record_conversation_turn(
    state: CatiaAgentState,
    assistant_message: str,
) -> list[dict[str, str]]:
    messages = list(
        state.get("messages", [])
    )

    messages.append(
        {
            "role": "user",
            "content": state["user_message"],
        }
    )

    messages.append(
        {
            "role": "assistant",
            "content": assistant_message,
        }
    )

    maximum_messages = (
        settings.max_conversation_history_messages
    )

    return messages[-maximum_messages:]


def _build_action_success_message(
    action_name: str,
    result_data: dict[str, Any] | None,
) -> str:
    data = result_data or {}

    if action_name == "open_cad_document":
        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"{document_name} was opened successfully."
            )

        return (
            "The CATIA document was opened successfully."
        )

    if action_name == "get_active_document":
        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"The active CATIA document is "
                f"{document_name}."
            )

        return (
            "CATIA does not currently have an active "
            "document."
        )

    if action_name == "create_cad_document":
        document_name = data.get(
            "documentName"
        )

        document_type = data.get(
            "documentType"
        )

        if document_name:
            return (
                f"{document_name} was created successfully."
            )

        if document_type:
            return (
                f"A new {document_type} document was "
                "created successfully."
            )

        return (
            "The CATIA document was created successfully."
        )

    if action_name == "close_cad_document":
        closed_documents = data.get(
            "closedDocuments"
        )

        if isinstance(
            closed_documents,
            list,
        ) and closed_documents:
            names = ", ".join(
                str(name)
                for name in closed_documents
            )

            return (
                f"{names} closed successfully."
            )

        document_name = data.get(
            "documentName"
        )

        if document_name:
            return (
                f"{document_name} closed successfully."
            )

        return (
            "The requested CATIA document was closed "
            "successfully."
        )

    if action_name == "download_cad":
        document_name = data.get(
            "documentName"
        )

        part_number = data.get(
            "partNumber"
        )

        part_revision = data.get(
            "partRevision"
        )

        if document_name:
            return (
                f"{document_name} was downloaded successfully."
            )

        if part_number and part_revision:
            return (
                f"{part_number} revision {part_revision} was "
                "downloaded successfully."
            )

        if part_number:
            return (
                f"{part_number} was downloaded successfully."
            )

        return (
            "The CAD file was downloaded successfully."
        )

    if action_name == "start_model":
        success_message = data.get(
            "success_message"
        )

        if success_message:
            return str(success_message)

        return (
            "The CATIA start model was launched successfully."
        )

    return (
        "The CATIA action completed successfully."
    )


def _build_action_failure_message(
    action_name: str,
    result: ActionExecutionResultRequest,
) -> str:
    error_message = (
        result.errorMessage
        or "The external CATIA executor reported a failure."
    )

    return (
        f"The CATIA action '{action_name}' failed. "
        f"{error_message}"
    )
