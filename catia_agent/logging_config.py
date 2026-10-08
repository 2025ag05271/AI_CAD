from __future__ import annotations

import json
import logging
import logging.config
import re
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any, Iterator
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
        # uvicorn duplicate of the message with ANSI color codes
        "color_message",
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

class DailyFileHandler(logging.Handler):
    """
    Append to <directory>/<prefix>_YYYYMMDD.log, switching to a new file
    at local midnight. Files are never renamed, so several processes can
    safely write to the same directory.
    """

    def __init__(
        self,
        directory: Path,
        prefix: str,
        retention_days: int,
    ) -> None:
        super().__init__()
        self.directory = directory
        self.prefix = prefix
        self.retention_days = retention_days
        self._current_date: date | None = None
        self._stream: IO[str] | None = None

    def path_for(self, day: date) -> Path:
        return self.directory / f"{self.prefix}_{day:%Y%m%d}.log"

    def _open_for(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()

        self.directory.mkdir(parents=True, exist_ok=True)
        self._stream = open(
            self.path_for(day),
            "a",
            encoding="utf-8",
        )
        self._current_date = day
        self._delete_expired_files(day)

    def _delete_expired_files(self, today: date) -> None:
        cutoff = today - timedelta(days=self.retention_days)

        for path in self.directory.glob(f"{self.prefix}_*.log"):
            try:
                file_day = datetime.strptime(
                    path.stem.rsplit("_", 1)[-1],
                    "%Y%m%d",
                ).date()
            except ValueError:
                continue

            if file_day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = date.today()

            if today != self._current_date or self._stream is None:
                self._open_for(today)

            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()

        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self.acquire()
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            self.release()

        super().close()


_OWNED_HANDLER_ATTRIBUTE = "_catia_agent_handler"


class _WindowsConnectionResetFilter(logging.Filter):
    """
    Drop asyncio's "Exception in callback _ProactorBasePipeTransport.
    _call_connection_lost" ConnectionResetError (WinError 10054). It is
    logged on Windows whenever a client closes a socket first and is
    harmless.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None:
            return True

        exception = record.exc_info[1]

        return not (
            isinstance(exception, ConnectionResetError)
            and "_call_connection_lost" in record.getMessage()
        )


def _build_file_handler(
    log_file_prefix: str,
    formatter: logging.Formatter,
) -> DailyFileHandler | None:
    directory = Path(settings.log_directory)

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"File logging disabled: cannot create {directory}: {exc}",
            file=sys.stderr,
        )
        return None

    handler = DailyFileHandler(
        directory=directory,
        prefix=log_file_prefix,
        retention_days=settings.log_file_retention_days,
    )
    handler.setLevel(settings.log_level)
    handler.setFormatter(formatter)
    return handler


def setup_logging(
    log_file_prefix: str = "CatiaAgentApi",
) -> None:
    """
    Configure application and dependency logging: console always, plus
    a daily file in settings.log_directory when file logging is enabled.

    This function is safe to call more than once.
    """

    formatter: logging.Formatter

    if settings.enable_json_logs:
        formatter = JsonFormatter()
    else:
        formatter = DevelopmentFormatter()

    console_handler = logging.StreamHandler(
        sys.stdout
    )
    console_handler.setLevel(settings.log_level)
    console_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [console_handler]
    log_file_path: str | None = None

    if settings.enable_file_logging:
        file_handler = _build_file_handler(
            log_file_prefix,
            formatter,
        )

        if file_handler is not None:
            handlers.append(file_handler)
            log_file_path = str(
                file_handler.path_for(date.today())
            )

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    for existing_handler in root_logger.handlers:
        if getattr(existing_handler, _OWNED_HANDLER_ATTRIBUTE, False):
            existing_handler.close()

    root_logger.handlers.clear()

    for handler in handlers:
        setattr(handler, _OWNED_HANDLER_ATTRIBUTE, True)
        root_logger.addHandler(handler)

    # Route uvicorn's own startup/error logs through the same handlers
    # (and therefore into the log file).
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    dependency_log_levels = {
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "asyncio": logging.WARNING,
        "multipart": logging.WARNING,
        # These dump full MCP payloads (every tool schema) at DEBUG.
        "mcp": logging.INFO,
        "sse_starlette": logging.INFO,
    }

    for (
        logger_name,
        log_level,
    ) in dependency_log_levels.items():
        dependency_logger = logging.getLogger(
            logger_name
        )
        dependency_logger.setLevel(log_level)

    asyncio_logger = logging.getLogger("asyncio")

    if not any(
        isinstance(existing_filter, _WindowsConnectionResetFilter)
        for existing_filter in asyncio_logger.filters
    ):
        asyncio_logger.addFilter(_WindowsConnectionResetFilter())

    logging.captureWarnings(True)

    log_event(
        get_logger(__name__),
        "logging_configured",
        logLevel=settings.log_level,
        logFile=log_file_path,
    )


def get_logger(
    name: str,
) -> logging.Logger:
    """
    Return a standard library logger.
    """

    return logging.getLogger(name)


# -------------------------------------------------------------------------
# Generic logging and timing helpers
#
# Use these from any module so log shape can be changed in one place.
# -------------------------------------------------------------------------

def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """
    Emit one structured log event with arbitrary fields.

    Example: log_event(logger, "mcp_tool_called", tool="open_cad_document")
    """

    # Field names that clash with LogRecord attributes would raise.
    safe_fields = {
        (
            f"field_{key}"
            if key in _RESERVED_LOG_ATTRIBUTES
            else key
        ): value
        for key, value in fields.items()
    }

    logger.log(
        level,
        event,
        extra=safe_fields,
        exc_info=exc_info,
        stacklevel=2,
    )


class Stopwatch:
    """
    Monotonic timer: sw = Stopwatch(); ...; sw.elapsed_ms
    """

    def __init__(self) -> None:
        self._started_at = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return round(
            (time.perf_counter() - self._started_at) * 1000,
            2,
        )


@contextmanager
def log_duration(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """
    Time a block and log "<event>_completed" or "<event>_failed" with
    durationMs. Works inside async functions too.

    The yielded dict can be filled with result fields to include:

        with log_duration(logger, "agent_run_start") as result:
            response = await start(...)
            result["status"] = response["status"]
    """

    stopwatch = Stopwatch()
    result_fields: dict[str, Any] = {}

    log_event(
        logger,
        f"{event}_started",
        level=logging.DEBUG,
        **fields,
    )

    try:
        yield result_fields

    except BaseException as exc:
        log_event(
            logger,
            f"{event}_failed",
            level=logging.WARNING,
            durationMs=stopwatch.elapsed_ms,
            exceptionType=type(exc).__name__,
            **fields,
            **result_fields,
        )
        raise

    log_event(
        logger,
        f"{event}_completed",
        level=level,
        durationMs=stopwatch.elapsed_ms,
        **fields,
        **result_fields,
    )