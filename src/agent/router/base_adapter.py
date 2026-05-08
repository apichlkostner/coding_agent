"""Abstract base class every adapter must implement.

An *adapter* bridges one external channel (Discord, terminal, Telegram, …)
and the :class:`~agent.router.router.MessageRouter`.  It is responsible for:

- Translating platform events into :class:`~agent.router.messages.InboundMessage`
  objects and forwarding them to the router via ``router.dispatch()``.
- Receiving :class:`~agent.router.messages.OutboundMessage` objects from the
  router and delivering them to the correct user/channel on the platform.
- Constructing ``thread_id`` values according to its own identity scheme.
- Applying any platform-specific formatting (chunking, Markdown conversion, …)
  inside ``send()``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agent.router.messages import OutboundMessage

if TYPE_CHECKING:
    from agent.router.router import MessageRouter


class BaseAdapter(ABC):
    """Contract that every channel adapter must fulfil.

    Subclasses **must** set ``adapter_id`` as a class attribute or instance
    attribute before calling ``router.register(self)``.

    Example
    -------
    .. code-block:: python

        class MyAdapter(BaseAdapter):
            adapter_id = "my_channel"

            async def start(self, router: MessageRouter) -> None:
                # subscribe to events, call router.dispatch() for each
                ...

            async def send(self, message: OutboundMessage) -> None:
                # deliver message.content to the appropriate destination
                ...
    """

    adapter_id: str

    @abstractmethod
    async def start(self, router: MessageRouter) -> None:
        """Begin listening for events and run until the adapter shuts down.

        Implementations should call ``await router.dispatch(inbound_message)``
        for every inbound event.  This method is expected to run for the
        lifetime of the adapter (i.e. it blocks until shutdown).

        Parameters
        ----------
        router:
            The :class:`~agent.router.router.MessageRouter` to dispatch
            incoming messages to.
        """

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Deliver *message* to the appropriate user/channel.

        The ``message.reply_channel_id`` and ``message.metadata["msg_type"]``
        fields provide the information needed for routing and formatting.

        Parameters
        ----------
        message:
            The outbound message produced by the agent service.
        """
