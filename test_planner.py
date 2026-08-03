import json
import asyncio
import ollama
from pydantic import ValidationError

from catia_agent.planner import plan_catia_action


def print_error(
    status: str,
    message: str,
    details=None,
) -> None:
    response = {
        "status": status,
        "message": message,
    }

    if details is not None:
        response["details"] = details

    print(
        json.dumps(
            response,
            indent=2,
            default=str,
        )
    )


async def main() -> None:
    user_command = input(
        "Enter a CATIA command: "
    ).strip()

    if not user_command:
        print_error(
            status="invalid_request",
            message="The CATIA command cannot be empty.",
        )
        return

    try:
        result = await plan_catia_action(user_command)

        print("\nPlanner result:")
        print(result.model_dump_json(indent=2))

    except ValidationError as exc:
        print("\nPlanner result:")

        print_error(
            status="invalid_model_output",
            message=(
                "Qwen returned a tool call that did not "
                "match the required tool schema."
            ),
            details=exc.errors(),
        )

    except ValueError as exc:
        print("\nPlanner result:")

        print_error(
            status="action_rejected",
            message=str(exc),
        )

    except ollama.ResponseError as exc:
        print("\nPlanner result:")

        print_error(
            status="model_unavailable",
            message=str(exc),
        )


if __name__ == "__main__":
    asyncio.run(main())