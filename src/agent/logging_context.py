import logging
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    message_id: str
    thread_id: str


DEFAULT_RUN_CONTEXT: RunContext = RunContext("-", "-")

run_context_var: ContextVar[RunContext] = ContextVar(
    "run_context", default=DEFAULT_RUN_CONTEXT
)


class ThreadIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rc: RunContext = run_context_var.get()
        record.message_id = rc.message_id
        record.thread_id = rc.thread_id
        return True
