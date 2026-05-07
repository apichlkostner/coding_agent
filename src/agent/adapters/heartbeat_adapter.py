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
    that prompt at a configurable interval.  All output is always written to
    the standard Python logger.  If ``settings.output_adapter_id`` and
    ``settings.output_channel_id`` are both non-empty, every outbound message
    is also forwarded to that adapter via
    :meth:`~agent.router.router.MessageRouter.send_to` — enabling heartbeat
    results to appear in a Discord channel, for example.

    This is the canonical example of an *agent-initiated* message flow: no
    human types anything — the adapter injects a synthetic
    :class:`~agent.router.messages.InboundMessage` on a schedule.

    Thread ID
    ---------
    All heartbeat runs share ``"heartbeat"`` so the LangGraph checkpointer
    maintains a single persistent conversation.

    Parameters
    ----------
    settings:
        :class:`~agent.config.HeartbeatSettings` instance.  Defaults to
        ``HeartbeatSettings()`` (600 s interval, ``HEARTBEAT.md`` file,
        no forwarding).
    """

    adapter_id = "heartbeat"

    def __init__(self, settings: HeartbeatSettings | None = None) -> None:
        self._settings = settings or HeartbeatSettings()
        self._router: MessageRouter | None = None

    async def start(self, router: MessageRouter) -> None:
        """Read the prompt file and run the agent periodically until cancelled."""
        self._router = router

        try:
            prompt = Path(self._settings.prompt_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(
                "HeartbeatAdapter: '%s' not found — heartbeat disabled.",
                self._settings.prompt_file,
            )
            return

        fwd = self._settings.output_adapter_id and self._settings.output_channel_id
        logger.info(
            "HeartbeatAdapter started (interval=%ss, file='%s', forward=%s).",
            self._settings.interval_seconds,
            self._settings.prompt_file,
            f"{self._settings.output_adapter_id}:{self._settings.output_channel_id}"
            if fwd
            else "log-only",
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
        """Log the agent's output and optionally forward it to another adapter.

        Logging always happens regardless of forwarding configuration.
        Forwarding is only performed when both ``output_adapter_id`` and
        ``output_channel_id`` are set in the adapter's
        :class:`~agent.config.HeartbeatSettings`.
        """
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

        # Forward to the configured output adapter if set.
        if self._settings.output_adapter_id and self._settings.output_channel_id:
            if self._router is not None:
                await self._router.send_to(
                    OutboundMessage(
                        adapter_id=self._settings.output_adapter_id,
                        reply_channel_id=self._settings.output_channel_id,
                        content=message.content,
                        metadata=dict(message.metadata),
                    )
                )
            else:
                logger.warning(
                    "HeartbeatAdapter: forwarding is configured but the router "
                    "is not available (send() called before start())."
                )
