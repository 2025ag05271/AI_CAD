import json
from pathlib import Path
from typing import Any, Literal

import ollama
from pydantic import BaseModel, ValidationError


MODEL_NAME = "qwen3:4b"


# ---------------------------------------------------------
# Typed action models
# ---------------------------------------------------------

class OpenCadDocumentArguments(BaseModel):
    filePath: str


class OpenCadDocumentAction(BaseModel):
    action: Literal["open_cad_document"]
    arguments: OpenCadDocumentArguments


class ActionRequiredResult(BaseModel):
    status: Literal["action_required"]
    action: OpenCadDocumentAction


class UnsupportedRequestResult(BaseModel):
    status: Literal["unsupported_request"]
    message: str


PlannerResult = ActionRequiredResult | UnsupportedRequestResult


# ---------------------------------------------------------
# Deterministic validation
# ---------------------------------------------------------

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
            f"{file_path.suffix}"
        )


# ---------------------------------------------------------
# Native tool definition supplied to Qwen
# ---------------------------------------------------------

OPEN_CAD_DOCUMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open_cad_document",
        "description": (
            "Open an existing CATIA CAD document using an "
            "absolute Windows file path. Use this tool only "
            "for CATPart, CATProduct, or CATDrawing files."
        ),
        "parameters": {
            "type": "object",
            "required": [
                "filePath",
            ],
            "properties": {
                "filePath": {
                    "type": "string",
                    "description": (
                        "Absolute Windows path of the CATIA "
                        "document to open."
                    ),
                }
            },
        },
    },
}


# ---------------------------------------------------------
# Ask Qwen to select a tool
# ---------------------------------------------------------

def plan_catia_action(
    user_command: str,
) -> PlannerResult:
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a CATIA command planner. "
                    "Select an available tool only when the user's "
                    "request matches that tool. "
                    "Do not invent file paths or arguments. "
                    "If no available tool supports the request, "
                    "briefly explain that the request is unsupported."
                ),
            },
            {
                "role": "user",
                "content": user_command,
            },
        ],
        tools=[
            OPEN_CAD_DOCUMENT_TOOL,
        ],
        think=False,
        options={
            "temperature": 0,
            "keep_alive" : "1h"
        },
    )

    message = response["message"]
    tool_calls = message.get("tool_calls") or []

    if not tool_calls:
        return UnsupportedRequestResult(
            status="unsupported_request",
            message=(
                "No available CATIA tool supports this request. "
                "Currently supported document types are CATPart, "
                "CATProduct, and CATDrawing."
            ),
        )

    if len(tool_calls) != 1:
        raise ValueError(
            "Expected exactly one tool call, but Qwen "
            f"returned {len(tool_calls)}."
        )

    tool_call = tool_calls[0]
    function = tool_call["function"]

    raw_action = {
        "action": function["name"],
        "arguments": function["arguments"],
    }

    action = OpenCadDocumentAction.model_validate(
        raw_action
    )

    validate_open_cad_document(action)

    return ActionRequiredResult(
        status="action_required",
        action=action,
    )


# ---------------------------------------------------------
# Program entry point
# ---------------------------------------------------------

def main() -> None:
    user_command = input(
        "Enter a CATIA command: "
    ).strip()

    if not user_command:
        print("\nPlanner result:")
        print(
            json.dumps(
                {
                    "status": "invalid_request",
                    "message": "The CATIA command cannot be empty.",
                },
                indent=2,
            )
        )
        return

    try:
        result = plan_catia_action(user_command)

        print("\nPlanner result:")
        print(result.model_dump_json(indent=2))

    except ValidationError as exc:
        print("\nPlanner result:")
        print(
            json.dumps(
                {
                    "status": "invalid_model_output",
                    "message": (
                        "Qwen returned a tool call that did not "
                        "match the required schema."
                    ),
                    "validationErrors": exc.errors(),
                },
                indent=2,
                default=str,
            )
        )

    except ValueError as exc:
        print("\nPlanner result:")
        print(
            json.dumps(
                {
                    "status": "action_rejected",
                    "message": str(exc),
                },
                indent=2,
            )
        )

    except ollama.ResponseError as exc:
        print("\nPlanner result:")
        print(
            json.dumps(
                {
                    "status": "model_unavailable",
                    "message": str(exc),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()