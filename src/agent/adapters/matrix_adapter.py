"""Matrix adapter — receives and sends Matrix messages via matrix-nio."""

from __future__ import annotations

import asyncio
import logging
import random

import nio

from agent.config import MatrixSettings
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

logger = logging.getLogger(__name__)


class MatrixAdapter(BaseAdapter):
    """Matrix channel adapter.

    Connects to a Matrix homeserver using a pre-issued access token and
    dispatches every ``m.room.message`` / ``m.text`` event from joined rooms
    to the :class:`~agent.router.router.MessageRouter`.

    The bot must be added to rooms manually by an admin; it does not
    auto-accept invitations.

    Only ``response`` and ``error`` outbound messages are delivered to Matrix.
    Intermediate ``tool_call`` / ``tool_result`` events are silently dropped.

    Responses are sent as plain text with Matrix reply threading
    (``m.in_reply_to``) referencing the original event ID, which is forwarded
    from ``InboundMessage.metadata["event_id"]`` through ``AgentService`` to
    every ``OutboundMessage``.

    Thread ID
    ---------
    ``matrix-{room_id}-{sender_id}`` — one persistent LangGraph thread per
    user per room.

    Parameters
    ----------
    settings:
        :class:`~agent.config.MatrixSettings` with homeserver URL, access
        token, and bot user ID.
    """

    adapter_id = "matrix"

    def __init__(self, settings: MatrixSettings) -> None:
        self._settings = settings
        self._client = nio.AsyncClient(settings.homeserver_url, settings.user_id)
        self._client.access_token = settings.access_token
        self._router: MessageRouter | None = None

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    async def start(self, router: MessageRouter) -> None:
        """Connect to Matrix and run the sync loop until cancelled."""
        self._router = router

        async def _on_message(room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
            # Ignore the bot's own messages to prevent loops.
            if event.sender == self._settings.user_id:
                return

            inbound = InboundMessage(
                adapter_id=self.adapter_id,
                thread_id=f"matrix-{room.room_id}-{event.sender}",
                content=event.body,
                reply_channel_id=room.room_id,
                user_id=event.sender,
                metadata={
                    "event_id": event.event_id,
                    "room_name": room.display_name or room.room_id,
                },
            )
            await router.dispatch(inbound)  # fire-and-forget

        self._client.add_event_callback(_on_message, nio.RoomMessageText)

        attempt = 0
        next_batch: str | None = None
        while True:
            try:
                response = await self._client.sync(
                    timeout=30_000,
                    full_state=False,
                    since=next_batch,
                )
                if isinstance(response, nio.SyncResponse):
                    next_batch = response.next_batch
                    attempt = 0
                else:
                    # SyncError — treat as a transient failure.
                    raise RuntimeError(f"Sync failed: {response}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                backoff = min(2**attempt + random.uniform(0, 1), 60.0)
                logger.error(
                    "MatrixAdapter sync error (retry in %.1fs): %s", backoff, exc
                )
                await asyncio.sleep(backoff)
                attempt += 1

    async def send(self, message: OutboundMessage) -> None:
        """Deliver *message* to the Matrix room identified by ``reply_channel_id``.

        Only ``response`` and ``error`` messages are sent; all other types are
        silently dropped. Replies are threaded using ``m.in_reply_to`` when the
        original ``event_id`` is available in the message metadata.
        """
        if message.msg_type not in ("response", "error"):
            return
        if not message.content:
            return

        content: dict[str, object] = {
            "msgtype": "m.text",
            "body": message.content,
        }
        event_id: str | None = message.metadata.get("event_id")
        if event_id:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": event_id}}

        response = await self._client.room_send(
            room_id=message.reply_channel_id,
            message_type="m.room.message",
            content=content,
        )
        if isinstance(response, nio.RoomSendError):
            logger.error(
                "MatrixAdapter: room_send failed for room %s: %s",
                message.reply_channel_id,
                response,
            )
