"""Tests for ``agent.logging_config`` — ``JsonFormatter`` and
``configure_logging`` wiring (handlers, filters, levels, idempotency).

Filter logic itself is covered in ``test_logging_context.py``; here we verify
that ``configure_logging`` wires everything together correctly.
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from agent.logging_config import JsonFormatter, configure_logging
from agent.logging_context import RunContext, run_context_var

NOISY_LOGGERS = ("httpx", "nio", "discord")


@pytest.fixture
def clean_root_logger(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Run each test in a tmp cwd and restore root logger state afterwards.

    Returns ``(root, configure)`` where ``configure()`` clears root handlers
    (pytest's logging plugin re-adds its capture handler at the call phase)
    and then invokes ``configure_logging``.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_noisy = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}

    def configure() -> None:
        root.handlers = []
        configure_logging()

    yield root, configure

    root.handlers = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_noisy.items():
        logging.getLogger(name).setLevel(level)


def _record(
    msg: str = "hello",
    level: int = logging.INFO,
    exc_info=None,
    **extra: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_emits_valid_json_with_expected_keys(self) -> None:
        line = JsonFormatter().format(_record("hello"))
        payload = json.loads(line)

        assert set(payload) == {
            "timestamp",
            "level",
            "message_id",
            "thread_id",
            "event",
            "message",
            "logger",
            "usage_metadata",
        }
        assert payload["level"] == "INFO"
        assert payload["message"] == "hello"
        assert payload["logger"] == "test"

    def test_defaults_when_context_attrs_absent(self) -> None:
        payload = json.loads(JsonFormatter().format(_record()))

        assert payload["message_id"] == "-"
        assert payload["thread_id"] == "-"
        assert payload["event"] == {}

    def test_event_attr_passed_through(self) -> None:
        event = {"tool": "read_file", "path": "/tmp/x"}
        payload = json.loads(JsonFormatter().format(_record(event=event)))

        assert payload["event"] == event

    def test_exception_included_when_exc_info_set(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            payload = json.loads(
                JsonFormatter().format(_record(exc_info=sys.exc_info()))
            )

        assert "exception" in payload
        assert "ValueError" in payload["exception"]
        assert "boom" in payload["exception"]

    def test_non_ascii_message_not_escaped(self) -> None:
        payload = json.loads(JsonFormatter().format(_record("héllo ✓")))

        assert payload["message"] == "héllo ✓"

    def test_message_args_interpolated(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="count=%d",
            args=(7,),
            exc_info=None,
        )

        payload = json.loads(JsonFormatter().format(record))

        assert payload["message"] == "count=7"


class TestConfigureLogging:
    def test_sets_root_level_and_silences_noisy_loggers(
        self, clean_root_logger
    ) -> None:
        root, configure = clean_root_logger
        configure()

        assert root.level == logging.INFO
        for name in NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING

    def test_adds_file_and_stderr_handlers(self, clean_root_logger) -> None:
        root, configure = clean_root_logger
        configure()

        handlers = root.handlers
        assert len(handlers) == 2

        file_handler = next(
            h for h in handlers if isinstance(h, TimedRotatingFileHandler)
        )
        stream_handler = next(
            h
            for h in handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, TimedRotatingFileHandler)
        )

        assert file_handler.level == logging.INFO
        assert file_handler.when == "MIDNIGHT"
        assert file_handler.backupCount == 14
        assert file_handler.utc is True
        assert file_handler.suffix == "%Y-%m-%d"
        assert isinstance(file_handler.formatter, JsonFormatter)
        assert any(
            isinstance(f, logging.Filter) and type(f).__name__ == "ThreadIdFilter"
            for f in file_handler.filters
        )

        assert stream_handler.level == logging.WARNING
        assert isinstance(stream_handler.formatter, JsonFormatter)
        assert any(
            isinstance(f, logging.Filter) and type(f).__name__ == "ThreadIdFilter"
            for f in stream_handler.filters
        )

    def test_is_idempotent(self, clean_root_logger) -> None:
        root, configure = clean_root_logger
        configure()
        first = [type(h) for h in root.handlers]

        configure()

        assert [type(h) for h in root.handlers] == first

    def test_end_to_end_json_log_file_with_stamped_context(
        self, clean_root_logger, capfd: pytest.CaptureFixture[str]
    ) -> None:
        root, configure = clean_root_logger
        configure()

        token = run_context_var.set(
            RunContext(message_id="msg-e2e", thread_id="thread-e2e")
        )
        try:
            logging.getLogger("test.e2e").info("e2e message")
            logging.getLogger("test.e2e").warning("e2e warning")
        finally:
            run_context_var.reset(token)

        for handler in root.handlers:
            handler.flush()

        file_handler = next(
            h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)
        )
        log_path = Path(file_handler.baseFilename)
        payloads = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        info_entry = next(p for p in payloads if p["message"] == "e2e message")
        assert info_entry["message_id"] == "msg-e2e"
        assert info_entry["thread_id"] == "thread-e2e"
        assert info_entry["level"] == "INFO"

        stderr = capfd.readouterr().err
        warning_entry = next(
            json.loads(line) for line in stderr.splitlines() if line.strip()
        )
        assert warning_entry["message"] == "e2e warning"
        assert warning_entry["level"] == "WARNING"
        assert warning_entry["message_id"] == "msg-e2e"
