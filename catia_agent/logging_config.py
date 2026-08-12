from __future__ import annotations

import json
import logging
import logging.config
import re
from collections.abc import Generator

import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from catia_agent.config import settings


# -------------------------------------------------------------------------
# Logging context
# -------------------------------------------------------------------------

request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)

session_id_context: ContextVar[str | None] = ContextVar(
    "session_id",
    default=None,
)

run_id_context: ContextVar[str | None] = ContextVar(
    "run_id",
    default=None,
)

action_id_context: ContextVar[str | None] = ContextVar(
    "action_id",
    default=None,
)


_CONTEXT_VARIABLES: dict[str, ContextVar[str | None]] = {
    "requestId": request_id_context,
    "sessionId": session_id_context,
    "runId": run_id_context,
    "actionId": action_id_context,
}


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

_REQUEST_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]+$"
)

_RESERVED_LOG_ATTRIBUTES = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "apiKey",
        "adminApiKey",
        "accessToken",
        "refreshToken",
        "password",
        "secret",
        "azureFoundryApiKey",
        "bearerToken",
    }
)

_REDACTED_VALUE = "[REDACTED]"


# -------------------------------------------------------------------------
# Context helpers
# -------------------------------------------------------------------------

def create_request_id() -> str:
    """
    Create a new request correlation identifier.
    """

    return str(uuid4())


def normalize_request_id(
    value: str | None,
) -> str:
    """
    Validate a caller-provided request identifier.

    Invalid identifiers are replaced with a generated value instead
    of being reflected into response headers and logs.
    """

    if value is None:
        return create_request_id()

    normalized_value = value.strip()

    if not normalized_value:
        return create_request_id()

    if (
        len(normalized_value)
        > settings.max_request_id_characters
    ):
        return create_request_id()

    if not _REQUEST_ID_PATTERN.fullmatch(
        normalized_value
    ):
        return create_request_id()

    return normalized_value


def get_logging_context() -> dict[str, str]:
    """
    Return the currently bound logging identifiers.
    """

    context: dict[str, str] = {}

    for field_name, context_variable in (
        _CONTEXT_VARIABLES.items()
    ):
        value = context_variable.get()

        if value is not None:
            context[field_name] = value

    return context


def clear_logging_context() -> None:
    """
    Clear all logging context values for the current task.
    """

    for context_variable in (
        _CONTEXT_VARIABLES.values()
    ):
        context_variable.set(None)


@contextmanager
def bind_logging_context(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    action_id: str | None = None,
) -> Generator[None]:

    """
    Temporarily bind identifiers to all logs emitted in the current
    asynchronous execution context.
    """

    values = {
        request_id_context: request_id,
        session_id_context: session_id,
        run_id_context: run_id,
        action_id_context: action_id,
    }

    tokens: list[
        tuple[
            ContextVar[str | None],
            Token[str | None],
        ]
    ] = []

    try:
        for context_variable, value in values.items():
            if value is not None:
                token = context_variable.set(value)
                tokens.append(
                    (
                        context_variable,
                        token,
                    )
                )

        yield

    finally:
        for context_variable, token in reversed(tokens):
            context_variable.reset(token)


# -------------------------------------------------------------------------
# Redaction and serialization
# -------------------------------------------------------------------------

def _is_sensitive_field(
    field_name: str,
) -> bool:
    normalized_name = field_name.replace(
        "_",
        "",
    ).lower()

    return any(
        sensitive_name.replace(
            "_",
            "",
        ).lower() == normalized_name
        for sensitive_name in _SENSITIVE_FIELD_NAMES
    )


def redact_log_value(
    field_name: str,
    value: Any,
) -> Any:
    """
    Redact explicitly sensitive structured-log fields.

    File paths and CATIA object names are not automatically redacted
    here. Callers should avoid logging them at INFO level.
    """

    if _is_sensitive_field(field_name):
        return _REDACTED_VALUE

    if isinstance(value, dict):
        return {
            key: redact_log_value(
                str(key),
                nested_value,
            )
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [
            redact_log_value(
                field_name,
                item,
            )
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            redact_log_value(
                field_name,
                item,
            )
            for item in value
        ]

    return value


def _serialize_log_value(
    value: Any,
) -> Any:
    """
    Convert common non-JSON values into safe representations.
    """

    if value is None:
        return None

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if isinstance(value, dict):
        return {
            str(key): _serialize_log_value(
                nested_value
            )
            for key, nested_value in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
            frozenset,
        ),
    ):
        return [
            _serialize_log_value(item)
            for item in value
        ]

    return str(value)


# -------------------------------------------------------------------------
# Formatters
# -------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """
    Convert every log record into one JSON object.
    """

    def format(
        self,
        record: logging.LogRecord,
    ) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        log_entry.update(
            get_logging_context()
        )

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_ATTRIBUTES:
                continue

            if key in log_entry:
                continue

            redacted_value = redact_log_value(
                key,
                value,
            )

            log_entry[key] = _serialize_log_value(
                redacted_value
            )

        if record.exc_info:
            log_entry["exception"] = (
                self.formatException(
                    record.exc_info
                )
            )

        if record.stack_info:
            log_entry["stack"] = record.stack_info

        return json.dumps(
            log_entry,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class DevelopmentFormatter(logging.Formatter):
    """
    Human-readable console output for development.
    """

    def format(
        self,
        record: logging.LogRecord,
    ) -> str:
        timestamp = datetime.now(
            timezone.utc
        ).isoformat(
            timespec="milliseconds"
        )

        context = get_logging_context()

        context_text = " ".join(
            f"{key}={value}"
            for key, value in context.items()
        )

        prefix = (
            f"{timestamp} "
            f"[{record.levelname}] "
            f"{record.name}"
        )

        if context_text:
            prefix = f"{prefix} [{context_text}]"

        message = (
            f"{prefix}: {record.getMessage()}"
        )

        extra_fields: dict[str, Any] = {}

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_ATTRIBUTES:
                continue

            if key in context:
                continue

            extra_fields[key] = redact_log_value(
                key,
                value,
            )

        if extra_fields:
            message += (
                " "
                + json.dumps(
                    extra_fields,
                    default=str,
                    ensure_ascii=False,
                )
            )

        if record.exc_info:
            message += (
                "\n"
                + self.formatException(
                    record.exc_info
                )
            )

        return message


# -------------------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------------------

def setup_logging() -> None:
    """
    Configure application and dependency logging.

    This function is safe to call more than once.
    """

    formatter: logging.Formatter

    if settings.enable_json_logs:
        formatter = JsonFormatter()
    else:
        formatter = DevelopmentFormatter()

    handler = logging.StreamHandler(
        sys.stdout
    )
    handler.setLevel(settings.log_level)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    dependency_log_levels = {
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "asyncio": logging.WARNING,
        "multipart": logging.WARNING,
    }

    for (
        logger_name,
        log_level,
    ) in dependency_log_levels.items():
        dependency_logger = logging.getLogger(
            logger_name
        )
        dependency_logger.setLevel(log_level)

    logging.captureWarnings(True)


def get_logger(
    name: str,
) -> logging.Logger:
    """
    Return a standard library logger.
    """

    return logging.getLogger(name)