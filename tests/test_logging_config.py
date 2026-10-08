import logging
from datetime import date, timedelta

import pytest

from catia_agent import logging_config
from catia_agent.logging_config import (
    DailyFileHandler,
    Stopwatch,
    log_duration,
    log_event,
)


def _file_handler(tmp_path, retention_days: int = 30) -> DailyFileHandler:
    handler = DailyFileHandler(
        directory=tmp_path / "nested" / "AI_Logs",
        prefix="Test",
        retention_days=retention_days,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def test_daily_file_handler_creates_missing_directory_and_writes(
    tmp_path,
) -> None:
    handler = _file_handler(tmp_path)
    logger = logging.getLogger("test_daily_file_handler")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        logger.info("hello_file")
    finally:
        logger.removeHandler(handler)
        handler.close()

    log_file = handler.path_for(date.today())
    assert log_file.exists()
    assert "hello_file" in log_file.read_text(encoding="utf-8")


def test_daily_file_handler_deletes_files_past_retention(tmp_path) -> None:
    handler = _file_handler(tmp_path, retention_days=7)
    handler.directory.mkdir(parents=True)

    old_file = handler.path_for(date.today() - timedelta(days=30))
    recent_file = handler.path_for(date.today() - timedelta(days=2))
    old_file.write_text("old", encoding="utf-8")
    recent_file.write_text("recent", encoding="utf-8")

    handler.emit(logging.makeLogRecord({"msg": "trigger"}))
    handler.close()

    assert not old_file.exists()
    assert recent_file.exists()


def test_setup_logging_adds_file_handler(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)

    monkeypatch.setattr(logging_config.settings, "enable_file_logging", True)
    monkeypatch.setattr(logging_config.settings, "log_directory", str(tmp_path))

    try:
        logging_config.setup_logging(log_file_prefix="UnitTest")

        file_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, DailyFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].path_for(date.today()).exists()

    finally:
        for handler in root_logger.handlers:
            handler.close()
        root_logger.handlers[:] = original_handlers


def test_log_event_renames_reserved_field_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_log_event")

    with caplog.at_level(logging.INFO):
        log_event(logger, "some_event", name="clash", tool="open")

    record = caplog.records[-1]
    assert record.getMessage() == "some_event"
    assert record.field_name == "clash"
    assert record.tool == "open"


def test_stopwatch_measures_elapsed_time() -> None:
    stopwatch = Stopwatch()
    assert stopwatch.elapsed_ms >= 0


def test_windows_connection_reset_noise_is_filtered() -> None:
    reset_filter = logging_config._WindowsConnectionResetFilter()

    def record_for(message: str, exception: BaseException):
        return logging.makeLogRecord(
            {
                "msg": message,
                "exc_info": (type(exception), exception, None),
            }
        )

    assert not reset_filter.filter(
        record_for(
            "Exception in callback "
            "_ProactorBasePipeTransport._call_connection_lost(None)",
            ConnectionResetError(10054, "forcibly closed"),
        )
    )
    assert reset_filter.filter(
        record_for("Something else", ConnectionResetError())
    )
    assert reset_filter.filter(logging.makeLogRecord({"msg": "plain"}))


@pytest.mark.asyncio
async def test_log_duration_logs_completed_with_result_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_log_duration")

    with caplog.at_level(logging.INFO):
        with log_duration(logger, "unit_work", runId="r1") as result:
            result["status"] = "completed"

    record = caplog.records[-1]
    assert record.getMessage() == "unit_work_completed"
    assert record.status == "completed"
    assert record.runId == "r1"
    assert record.durationMs >= 0


def test_log_duration_logs_failed_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_log_duration_failure")

    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError):
            with log_duration(logger, "unit_work"):
                raise ValueError("boom")

    record = caplog.records[-1]
    assert record.getMessage() == "unit_work_failed"
    assert record.levelno == logging.WARNING
    assert record.exceptionType == "ValueError"
