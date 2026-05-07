"""Discord adapter — receives and sends Discord messages via discord.py."""

from __future__ import annotations

import logging

import discord

from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

logger = logging.getLogger(__name__)

# Discord's hard limit for a single message.
_DISCORD_MAX_LEN = 2000


class _DiscordClient(discord.Client):
    """Internal ``discord.Client`` subclass that forwards events to the adapter.

    Using composition (adapter *has a* client) rather than multiple inheritance
    avoids the ``start(token)`` vs ``BaseAdapter.start(router)`` signature clash.
    """

    def __init__(self, adapter: "DiscordAdapter", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._adapter = adapter

    async def on_ready(self) -> None:
        logger.info("Discord ready: %s (ID: %s)", self.user, self.user.id if self.user else "?")

    async def on_message(self, message: discord.Message) -> None:
        logger.info("%s: %s", message.author, message.content)
        if message.author.bot:
            return
        await self._adapter._handle_message(message)


class DiscordAdapter(BaseAdapter):
    """Discord channel adapter.

    Receives messages from any Discord channel the bot can see and routes
    them through the :class:`~agent.router.router.MessageRouter`.

    The bot shows Discord's "typing…" indicator for the full duration of
    agent processing by awaiting the dispatch task inside ``channel.typing()``.

    Verbosity
    ---------
    Only ``response`` and ``error`` messages are delivered to Discord.
    Intermediate ``tool_call`` / ``tool_result`` events are discarded by
    :meth:`send` — Discord users see only the final answer.

    Thread ID
    ---------
    ``discord-{user_id}-{channel_id}`` — one persistent LangGraph thread per
    user per channel.

    Parameters
    ----------
    token:
        Discord bot token (``DISCORD_BOT_TOKEN`` env var).
    """

    adapter_id = "discord"

    def __init__(self, token: str) -> None:
        self._token = token
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = _DiscordClient(self, intents=intents)
        self._router: MessageRouter | None = None

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    async def start(self, router: MessageRouter) -> None:
        """Connect to Discord and start processing events."""
        self._router = router
        await self._client.start(self._token)

    async def send(self, message: OutboundMessage) -> None:
        """Deliver *message* to the Discord channel identified by ``reply_channel_id``.

        Only ``response`` and ``error`` messages are sent; all other types are
        silently dropped (tool steps are internal detail not shown to users).
        Long messages are split into ≤ 2 000-character chunks.
        """
        if message.msg_type not in ("response", "error"):
            return
        if not message.content:
            return

        channel = self._client.get_channel(int(message.reply_channel_id))
        if channel is None:
            try:
                channel = await self._client.fetch_channel(int(message.reply_channel_id))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "DiscordAdapter: could not get channel %s: %s",
                    message.reply_channel_id,
                    exc,
                )
                return

        content = message.content
        for i in range(0, len(content), _DISCORD_MAX_LEN):
            await channel.send(content[i : i + _DISCORD_MAX_LEN])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_message(self, message: discord.Message) -> None:
        """Build an :class:`InboundMessage` and dispatch it, holding the typing indicator."""
        if self._router is None:
            logger.error("DiscordAdapter._handle_message called before router was set.")
            return

        inbound = InboundMessage(
            adapter_id=self.adapter_id,
            thread_id=f"discord-{message.author.id}-{message.channel.id}",
            content=message.content,
            reply_channel_id=str(message.channel.id),
            user_id=str(message.author.id),
        )
        # Keep the typing indicator active until the agent finishes.
        async with message.channel.typing():
            task = await self._router.dispatch(inbound)
            await task
