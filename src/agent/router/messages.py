"""Message envelope types used by the router layer.

Every adapter converts its platform-specific events into ``InboundMessage``
and receives ``OutboundMessage`` objects from the router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Narrow type for the well-known "msg_type" metadata key.
MessageType = Literal["tool_call", "tool_result", "response", "error"]


@dataclass
class InboundMessage:
    """A normalised message arriving from any adapter.

    Attributes
    ----------
    adapter_id:
        Identifier of the adapter that produced this message
        (e.g. ``"discord"``, ``"terminal"``, ``"heartbeat"``).
    thread_id:
        Unique key passed to the LangGraph checkpointer so the right
        conversation history is loaded.  Each adapter is responsible for
        constructing this according to its own scheme
        (e.g. ``"discord-{user_id}-{channel_id}"``).
    content:
        Raw text to send to the agent as a ``HumanMessage``.
    reply_channel_id:
        Adapter-specific destination for the response.  What this string
        means is entirely up to the adapter (Discord channel ID, ``"stdout"``,
        a WebSocket session key, …).
    user_id:
        Human user identifier.  ``None`` for agent-initiated triggers such
        as the heartbeat.
    metadata:
        Arbitrary adapter-specific extras (guild ID, message ID, …).
    """

    adapter_id: str
    thread_id: str
    content: str
    reply_channel_id: str
    user_id: str | None = None
    metadata: dict = field(default_factory=dict)  # type: ignore[type-arg]


@dataclass
class OutboundMessage:
    """A normalised message that the router delivers via an adapter.

    Attributes
    ----------
    adapter_id:
        Which adapter should deliver this message.
    reply_channel_id:
        Adapter-specific destination (mirrors ``InboundMessage.reply_channel_id``
        for response routing, but can differ for agent-initiated broadcasts).
    content:
        Text content to deliver.
    metadata:
        Well-known key: ``"msg_type"`` (``MessageType``) classifies the
        message so adapters can apply platform-appropriate formatting.
        Additional keys are adapter-specific.
    """

    adapter_id: str
    reply_channel_id: str
    content: str
    metadata: dict = field(default_factory=dict)  # type: ignore[type-arg]

    @property
    def msg_type(self) -> MessageType | None:
        """Convenience accessor for the ``msg_type`` metadata key."""
        return self.metadata.get("msg_type")  # type: ignore[return-value]
