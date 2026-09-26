"""Heartbeat adapter — periodic agent-initiated runs driven by a Markdown prompt."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agent.config import Heartbeat
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter
from agent.tools.tools_notifications import consume_notifications

logger = logging.getLogger(__name__)


class HeartbeatAdapter(BaseAdapter):
    """Periodic, agent-initiated adapter.

    Reads a Markdown prompt file once at startup, then invokes the agent on
    that prompt at a configurable interval.  All output is always written to
    the standard Python logger.

    The agent controls *outbound forwarding* explicitly by calling the
    :func:`~agent.tools.tools_notifications.send_notification` tool — when it does,
    the message is forwarded to ``config.output_adapter`` /
    ``config.output_channel``.  Normal response text is logged only;
    nothing is forwarded unless the agent asks for it.

    This is the canonical example of an *agent-initiated* message flow: no
    human types anything — the adapter injects a synthetic
    :class:`~agent.router.messages.InboundMessage` on a schedule.

    Thread ID
    ---------
    All heartbeat runs share ``"heartbeat"`` so the LangGraph checkpointer
    maintains a single persistent conversation.

    Parameters
    ----------
    config:
        :class:`~agent.config.HeartbeatSettings` instance.  Defaults to
        ``HeartbeatSettings()`` (600 s interval, ``HEARTBEAT.md`` file,
        no forwarding).
    """

    adapter_id = "heartbeat"

    def __init__(self, config: Heartbeat | None = None) -> None:
        self._config = config or Heartbeat()
        self._router: MessageRouter | None = None

    async def start(self, router: MessageRouter) -> None:
        """Read the prompt file and run the agent periodically until cancelled."""
        self._router = router

        try:
            prompt = Path(self._config.prompt_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(
                "HeartbeatAdapter: '%s' not found — heartbeat disabled.",
                self._config.prompt_file,
            )
            return

        fwd = self._config.output_adapter and self._config.output_channel
        logger.info(
            "HeartbeatAdapter started (interval=%ss, file='%s', forward=%s).",
            self._config.interval,
            self._config.prompt_file,
            f"{self._config.output_adapter}:{self._config.output_channel}"
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

            await self._maybe_forward_notifications()
            await asyncio.sleep(self._config.interval)

    async def _maybe_forward_notifications(self) -> bool:
        """Forward any notifications the agent queued during this run.

        Returns ``True`` if at least one notification was forwarded.
        """
        if (
            not self._config.output_adapter
            or not self._config.output_channel
            or self._router is None
        ):
            consume_notifications()  # drain buffer even when forwarding disabled
            return False

        forwarded = False
        for notification in consume_notifications():
            await self._router.send_to(
                OutboundMessage(
                    adapter_id=self._config.output_adapter,
                    reply_channel_id=self._config.output_channel,
                    content=notification,
                    metadata={"msg_type": "response"},
                )
            )
            forwarded = True
        return forwarded

    async def send(self, message: OutboundMessage) -> None:
        """Log the agent's output (no automatic forwarding).

        Logging always happens.  Forwarding to the configured output channel
        is *not* done here — it is handled after each run by consuming the
        notification buffer that the agent populated via the
        :func:`~agent.tools.tools_notifications.send_notification` tool.
        """
        node = message.metadata.get("node_name") or "agent"

        if message.msg_type == "tool_call":
            logger.debug("[%s] \u2192 %s", node, message.content)
        elif message.msg_type == "tool_result":
            logger.debug("[%s] \u2190 %s", node, message.content)
        elif message.msg_type == "response":
            logger.debug(
                "[%s] %s", node, message.content, extra={"event": "chat-response"}
            )
        elif message.msg_type == "error":
            logger.error("Heartbeat error: %s", message.content)
        else:
            logger.debug("%s", message.content)
