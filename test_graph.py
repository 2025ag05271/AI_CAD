import json

import ollama
from pydantic import ValidationError

from catia_agent.graph import run_catia_agent


def main() -> None:
    user_command = input(
        "Enter a CATIA command: "
    )

    try:
        response = run_catia_agent(
            session_id="test-session-001",
            user_message=user_command,
        )

        print("\nLangGraph response:")

        print(
            json.dumps(
                response,
                indent=2,
                default=str,
            )
        )

    except ValidationError as exc:
        print("\nLangGraph execution failed:")

        print(
            json.dumps(
                {
                    "status": "invalid_model_output",
                    "message": (
                        "Qwen returned an invalid tool call."
                    ),
                    "details": exc.errors(),
                },
                indent=2,
                default=str,
            )
        )

    except ValueError as exc:
        print("\nLangGraph execution failed:")

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
        print("\nLangGraph execution failed:")

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