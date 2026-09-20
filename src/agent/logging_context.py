import logging
from contextvars import ContextVar

thread_id_var: ContextVar[str] = ContextVar("thread_id", default="-")


class ThreadIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.thread_id = thread_id_var.get()
        return True
