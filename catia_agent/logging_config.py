import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from catia_agent.config import settings


request_id_context: ContextVar[str | None] = (
    ContextVar(
        "request_id",
        default=None,
    )
)

session_id_context: ContextVar[str | None] = (
    ContextVar(
        "session_id",
        default=None,
    )
)

run_id_context: ContextVar[str | None] = ContextVar(
    "run_id",
    default=None,
)

action_id_context: ContextVar[str | None] = (
    ContextVar(
        "action_id",
        default=None,
    )
)


class JsonFormatter(logging.Formatter):
    """
    Convert every application log record into one JSON object.
    """

    reserved_attributes = {
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

        request_id = request_id_context.get()
        session_id = session_id_context.get()
        run_id = run_id_context.get()
        action_id = action_id_context.get()

        if request_id:
            log_entry["requestId"] = request_id

        if session_id:
            log_entry["sessionId"] = session_id

        if run_id:
            log_entry["runId"] = run_id

        if action_id:
            log_entry["actionId"] = action_id

        for key, value in record.__dict__.items():
            if key not in self.reserved_attributes:
                log_entry[key] = value

        if record.exc_info:
            log_entry["exception"] = (
                self.formatException(
                    record.exc_info
                )
            )

        return json.dumps(
            log_entry,
            default=str,
            ensure_ascii=False,
        )


class DevelopmentFormatter(logging.Formatter):
    """
    More readable console output for local development.
    """

    def format(
        self,
        record: logging.LogRecord,
    ) -> str:
        request_id = request_id_context.get()

        prefix = (
            f"[{record.levelname}] "
            f"{record.name}: "
        )

        if request_id:
            prefix += f"[{request_id}] "

        message = prefix + record.getMessage()

        if record.exc_info:
            message += "\n" + self.formatException(
                record.exc_info
            )

        return message


def setup_logging() -> None:
    root_logger = logging.getLogger()

    root_logger.handlers.clear()
    root_logger.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)

    if settings.enable_json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            DevelopmentFormatter()
        )

    root_logger.addHandler(handler)

    # Reduce noisy protocol-library logs unless debugging.
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )

    logging.getLogger("httpcore").setLevel(
        logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def create_request_id() -> str:
    return str(uuid4())