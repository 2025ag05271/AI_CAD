import json
from typing import Literal
from pathlib import Path
import ollama
from pydantic import BaseModel


class OpenDocumentArguments(BaseModel):
    filePath: str


class OpenDocumentAction(BaseModel):
    action: Literal["open_document"]
    arguments: OpenDocumentArguments
    

def validate_open_document_action(action: OpenDocumentAction) -> None:
    file_path = Path(action.arguments.filePath)

    allowed_extensions = {
        ".catpart",
        ".catproduct",
        ".catdrawing",
    }

    if not file_path.is_absolute():
        raise ValueError("The CATIA document path must be absolute.")

    if file_path.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Unsupported CATIA document extension: {file_path.suffix}"
        )


user_command = input("Enter a CATIA command: ")

prompt = f"""
You are a CATIA command planner.

Convert the user command into exactly one JSON action.

Allowed action:
open_document(filePath: string)

User command:
{user_command}

Return only valid JSON in this format:
{{
  "action": "open_document",
  "arguments": {{
    "filePath": "string"
  }}
}}
"""

response = ollama.chat(
    model="qwen3:4b",
    messages=[
        {
            "role": "user",
            "content": prompt,
        }
    ],
    format="json",
    think=False,
)

content = response["message"]["content"]

action = OpenDocumentAction.model_validate_json(content)

validate_open_document_action(action)

print("\nValidated CATIA action:")
print(action.model_dump_json(indent=2))