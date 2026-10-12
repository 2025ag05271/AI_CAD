from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Literal

import truststore
from pydantic import BaseModel, ConfigDict, ValidationError

truststore.inject_into_ssl()
os.environ["CATIA_AGENT_RUNTIME_MODE"] = "worker"

from catia_agent.graph import (
    AgentRunNotFoundError,
    AgentRunStateConflictError,
    resume_catia_agent,
    start_catia_agent,
)
from catia_agent.logging_config import (
    bind_logging_context,
    get_logger,
    setup_logging,
)
from catia_agent.model_providers import (
    ModelProviderError,
    ModelProviderTimeoutError,
    model_client,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    AgentRunRequest,
    RequestIdentifier,
)
from catia_agent.intent_classifier import warmup_intent_classifier


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1_048_576

logger = get_logger(__name__)


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocolVersion: Literal[1]
    requestId: RequestIdentifier
    type: Literal["start_run", "action_result", "shutdown"]
    payload: dict[str, Any] = {}


class ActionResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    runId: RequestIdentifier
    actionResult: ActionExecutionResultRequest


def _write_message(message: dict[str, Any]) -> None:
    encoded = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def _error_response(
    request_id: str | None,
    *,
    status: int,
    error_code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "type": "error",
        "payload": {
            "status": status,
            "errorCode": error_code,
            "message": message,
        },
    }


async def handle_message(
    raw_message: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    request_id = raw_message.get("requestId")
    if not isinstance(request_id, str):
        request_id = None

    try:
        request = WorkerRequest.model_validate(raw_message)
    except ValidationError:
        return (
            _error_response(
                request_id,
                status=422,
                error_code="REQUEST_VALIDATION_FAILED",
                message="The worker request did not match the JSON protocol.",
            ),
            False,
        )

    if request.type == "shutdown":
        return (
            {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request.requestId,
                "type": "shutdown_complete",
                "payload": {},
            },
            True,
        )

    with bind_logging_context(request_id=request.requestId):
        try:
            if request.type == "start_run":
                agent_request = AgentRunRequest.model_validate(
                    request.payload
                )
                result = await start_catia_agent(
                    session_id=agent_request.sessionId,
                    user_message=agent_request.message,
                    catia_context=agent_request.catiaContext,
                )
            else:
                action_payload = ActionResultPayload.model_validate(
                    request.payload
                )
                result = await resume_catia_agent(
                    action_payload.runId,
                    action_payload.actionResult,
                )

            return (
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "requestId": request.requestId,
                    "type": "agent_response",
                    "payload": result,
                },
                False,
            )

        except ValidationError:
            return (
                _error_response(
                    request.requestId,
                    status=422,
                    error_code="REQUEST_VALIDATION_FAILED",
                    message="The request payload did not match the Agent contract.",
                ),
                False,
            )
        except AgentRunStateConflictError as error:
            return (
                _error_response(
                    request.requestId,
                    status=409,
                    error_code="AGENT_RUN_STATE_CONFLICT",
                    message=str(error),
                ),
                False,
            )
        except AgentRunNotFoundError as error:
            return (
                _error_response(
                    request.requestId,
                    status=404,
                    error_code="AGENT_RUN_NOT_FOUND",
                    message=str(error),
                ),
                False,
            )
        except ModelProviderTimeoutError:
            return (
                _error_response(
                    request.requestId,
                    status=504,
                    error_code="MODEL_REQUEST_TIMED_OUT",
                    message="The configured model did not respond in time.",
                ),
                False,
            )
        except ModelProviderError:
            return (
                _error_response(
                    request.requestId,
                    status=503,
                    error_code="MODEL_PROVIDER_FAILED",
                    message="The configured model provider is unavailable.",
                ),
                False,
            )
        except Exception as error:
            logger.exception(
                "worker_request_failed",
                extra={"exceptionType": type(error).__name__},
            )
            return (
                _error_response(
                    request.requestId,
                    status=500,
                    error_code="INTERNAL_SERVER_ERROR",
                    message="The Agent worker could not process the request.",
                ),
                False,
            )


async def _read_message() -> bytes:
    return await asyncio.to_thread(
        sys.stdin.buffer.readline,
        MAX_MESSAGE_BYTES + 1,
    )


async def _discard_oversized_line(line: bytes) -> None:
    while line and not line.endswith(b"\n"):
        line = await asyncio.to_thread(
            sys.stdin.buffer.readline,
            65_536,
        )


async def run_worker() -> None:
    setup_logging(log_file_prefix="CatiaAgentWorker")
    for handler in logging.getLogger().handlers:
        if type(handler) is logging.StreamHandler:
            handler.setStream(sys.stderr)

    await warmup_intent_classifier()

    _write_message(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": "ready",
            "payload": {},
        }
    )

    while True:
        line = await _read_message()
        if not line:
            break

        if len(line) > MAX_MESSAGE_BYTES:
            await _discard_oversized_line(line)
            _write_message(
                _error_response(
                    None,
                    status=413,
                    error_code="REQUEST_BODY_TOO_LARGE",
                    message="The JSON message exceeds the worker size limit.",
                )
            )
            continue

        try:
            raw_message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _write_message(
                _error_response(
                    None,
                    status=400,
                    error_code="INVALID_JSON_MESSAGE",
                    message="The worker input was not valid UTF-8 JSON.",
                )
            )
            continue

        if not isinstance(raw_message, dict):
            _write_message(
                _error_response(
                    None,
                    status=400,
                    error_code="INVALID_JSON_MESSAGE",
                    message="Each worker message must be a JSON object.",
                )
            )
            continue

        response, should_exit = await handle_message(raw_message)
        _write_message(response)
        if should_exit:
            break


async def _run_and_close() -> None:
    try:
        await run_worker()
    finally:
        close_model_client = getattr(model_client, "aclose", None)
        if close_model_client is not None:
            await close_model_client()


def main() -> None:
    asyncio.run(_run_and_close())


if __name__ == "__main__":
    main()