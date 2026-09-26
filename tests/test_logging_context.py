"""Tests for ``agent.logging_context`` — the run-context ContextVar and the
``ThreadIdFilter`` that stamps ``message_id`` / ``thread_id`` onto log records.
"""

from __future__ import annotations

import io
import logging
from dataclasses import FrozenInstanceError

import pytest

from agent.logging_context import (
    DEFAULT_RUN_CONTEXT,
    RunContext,
    ThreadIdFilter,
    run_context_var,
)


def _record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def _stamped(
    record: logging.LogRecord,
) -> tuple[str | None, str | None]:
    """Return the ``(message_id, thread_id)`` the filter stamped on *record*."""
    return (
        getattr(record, "message_id", None),
        getattr(record, "thread_id", None),
    )


def test_run_context_is_frozen() -> None:
    ctx = RunContext(message_id="a", thread_id="b")

    with pytest.raises(FrozenInstanceError):
        ctx.message_id = "c"  # type: ignore[misc]


def test_filter_always_returns_true() -> None:
    assert ThreadIdFilter().filter(_record()) is True


def test_filter_uses_default_context() -> None:
    record = _record()
    ThreadIdFilter().filter(record)

    assert _stamped(record) == (
        DEFAULT_RUN_CONTEXT.message_id,
        DEFAULT_RUN_CONTEXT.thread_id,
    )


def test_filter_stamps_active_context() -> None:
    token = run_context_var.set(RunContext(message_id="msg-1", thread_id="thread-1"))
    try:
        record = _record()
        ThreadIdFilter().filter(record)

        assert _stamped(record) == ("msg-1", "thread-1")
    finally:
        run_context_var.reset(token)


def test_filter_reads_context_at_call_time() -> None:
    """The filter must resolve the ContextVar per record, not cache it."""
    filt = ThreadIdFilter()

    first = _record()
    filt.filter(first)

    token = run_context_var.set(RunContext(message_id="msg-2", thread_id="thread-2"))
    try:
        second = _record()
        filt.filter(second)
    finally:
        run_context_var.reset(token)

    assert _stamped(first) == (
        DEFAULT_RUN_CONTEXT.message_id,
        DEFAULT_RUN_CONTEXT.thread_id,
    )
    assert _stamped(second) == ("msg-2", "thread-2")


def test_filter_through_real_logger_and_formatter() -> None:
    """``message_id`` / ``thread_id`` must reach a real formatter."""
    logger = logging.getLogger("test.logging_context.integration")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message_id)s|%(thread_id)s|%(message)s"))
    handler.addFilter(ThreadIdFilter())
    logger.addHandler(handler)

    token = run_context_var.set(RunContext(message_id="msg-3", thread_id="thread-3"))
    try:
        logger.info("hello")
    finally:
        run_context_var.reset(token)
        logger.removeHandler(handler)

    assert stream.getvalue() == "msg-3|thread-3|hello\n"
