"""Heartbeat adapter — periodic agent-initiated runs driven by a Markdown prompt."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agent.config import HeartbeatSettings
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

logger = logging.getLogger(__name__)


class HeartbeatAdapter(BaseAdapter):
    """Periodic, agent-initiated adapter.

    Reads a Markdown prompt file once at startup, then invokes the agent on
    that prompt at a configurable interval.  All output is written to the
    standard Python logger (``agent.adapters.heartbeat_adapter``).

    This is the first example of an *agent-initiated* message flow: no human
    types anything — the adapter injects a synthetic
    :class:`~agent.router.messages.InboundMessage` on a schedule.

    Thread ID
    ---------
    All heartbeat runs share ``"heartbeat"`` so the LangGraph checkpointer
    maintains a single persistent conversation.

    Parameters
    ----------
    settings:
        :class:`~agent.config.HeartbeatSettings` instance.  Defaults to
        ``HeartbeatSettings()`` (600 s interval, ``HEARTBEAT.md`` file).
    """

    adapter_id = "heartbeat"

    def __init__(self, settings: HeartbeatSettings | None = None) -> None:
        self._settings = settings or HeartbeatSettings()

    async def start(self, router: MessageRouter) -> None:
        """Read the prompt file and run the agent periodically until cancelled."""
        try:
            prompt = Path(self._settings.prompt_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(
                "HeartbeatAdapter: '%s' not found — heartbeat disabled.",
                self._settings.prompt_file,
            )
            return

        logger.info(
            "HeartbeatAdapter started (interval=%ss, file='%s').",
            self._settings.interval_seconds,
            self._settings.prompt_file,
        )

        while True:
            inbound = InboundMessage(
                adapter_id=self.adapter_id,
                thread_id="heartbeat",
                content=prompt,
                reply_channel_id="log",
                user_id=None,
            )
            task = await router.dispatch(inbound)
            await task  # wait for agent to finish before sleeping
            await asyncio.sleep(self._settings.interval_seconds)

    async def send(self, message: OutboundMessage) -> None:
        """Log the agent's output at the appropriate level."""
        node = message.metadata.get("node_name") or "agent"

        if message.msg_type == "tool_call":
            logger.info("[%s] \u2192 %s", node, message.content)
        elif message.msg_type == "tool_result":
            logger.info("[%s] \u2190 %s", node, message.content)
        elif message.msg_type == "response":
            logger.info("[%s] %s", node, message.content)
        elif message.msg_type == "error":
            logger.error("Heartbeat error: %s", message.content)
        else:
            logger.info("%s", message.content)
