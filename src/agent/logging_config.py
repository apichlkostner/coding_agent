import json
import logging
from logging.handlers import TimedRotatingFileHandler

from agent.logging_context import ThreadIdFilter


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "thread_id": getattr(record, "thread_id", "-"),
            "event": {},
            "message": record.getMessage(),
            "logger": record.name,
        }

        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()

    if root.handlers:
        return

    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("nio").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)

    formatter = JsonFormatter()

    # rotating filename per day
    file_handler = TimedRotatingFileHandler(
        "logs/agent.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ThreadIdFilter())

    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(ThreadIdFilter())

    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
