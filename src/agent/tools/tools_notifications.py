"""In-memory notification buffer for agent-initiated alerts.

The agent queues messages via the ``send_notification`` tool during a graph
run.  After the run completes, the calling adapter (e.g. heartbeat) drains
the buffer with ``consume_notifications()`` and forwards each message to the
configured output channel.
"""

from __future__ import annotations

from langchain_core.tools import tool

# ── In-memory notification buffer ──────────────────────────────────────────
_notifications: list[str] = []


def consume_notifications() -> list[str]:
    """Return all pending notifications and clear the buffer.

    Called by :class:`~agent.adapters.heartbeat_adapter.HeartbeatAdapter`
    after each agent run.  Each string is a message that should be forwarded
    to the configured output channel.
    """
    items = list(_notifications)
    _notifications.clear()
    return items


@tool
def send_notification(content: str) -> str:
    """Send a message to the user via the configured output channel.

    Use this when you need to alert the user about something important,
    like a state change.  *Only call this when there is something worth
    reporting* -- if nothing changed, simply return an empty or minimal
    response and do NOT call this tool.

    Args:
        content: The message content to deliver.

    Returns:
        "ok" on success.
    """
    _notifications.append(content)
    return "ok"
