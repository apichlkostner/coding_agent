"""Central message router.

The :class:`MessageRouter` is the hub that connects adapters to the agent:

- Adapters push inbound events via :meth:`dispatch`.
- The router runs the agent through :class:`~agent.router.agent_service.AgentService`
  and fans the resulting :class:`~agent.router.messages.OutboundMessage` objects
  back to the originating adapter.
- Agent-initiated messages (heartbeat, notifications) can be pushed directly
  via :meth:`send_to` without going through the agent pipeline.

Thread safety
-------------
Messages on the **same** ``thread_id`` are processed sequentially (one
``asyncio.Lock`` per thread) to prevent checkpointer races.  Messages on
different thread IDs run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from agent.router.agent_service import AgentService
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

# Maximum number of per-thread locks kept in memory at once.  Least-recently-used
# entries are evicted when the limit is exceeded, provided the lock is not held.
_MAX_THREAD_LOCKS = 1_000


class MessageRouter:
    """Hub that connects :class:`~agent.router.base_adapter.BaseAdapter` instances
    to :class:`~agent.router.agent_service.AgentService`.

    Parameters
    ----------
    agent_service:
        The service that wraps the LangGraph graph.
    """

    def __init__(self, agent_service: AgentService) -> None:
        self._agent_service = agent_service
        self._adapters: dict[str, BaseAdapter] = {}
        # One asyncio.Lock per thread_id prevents concurrent writes to the
        # same LangGraph checkpointer thread.  The OrderedDict is used as a
        # bounded LRU cache: the oldest *unlocked* entry is evicted whenever
        # the dict exceeds _MAX_THREAD_LOCKS entries.
        self._thread_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, adapter: BaseAdapter) -> None:
        """Register *adapter* with the router.

        Parameters
        ----------
        adapter:
            Must have a unique ``adapter_id`` among all registered adapters.

        Raises
        ------
        ValueError
            If an adapter with the same ``adapter_id`` is already registered.
        """
        if adapter.adapter_id in self._adapters:
            raise ValueError(
                f"Adapter '{adapter.adapter_id}' is already registered. "
                "Use a unique adapter_id for each adapter instance."
            )
        self._adapters[adapter.adapter_id] = adapter
        logger.info("Registered adapter: %s", adapter.adapter_id)

    # ------------------------------------------------------------------
    # Inbound path  (adapter → agent → adapter)
    # ------------------------------------------------------------------

    async def dispatch(self, message: InboundMessage) -> asyncio.Task[None]:
        """Schedule processing of *message* as a background task.

        The call returns immediately (fire-and-forget from the adapter's point
        of view).  The returned :class:`asyncio.Task` can be awaited in tests
        to synchronise on completion.

        Parameters
        ----------
        message:
            The inbound message to process.

        Returns
        -------
        asyncio.Task
            Background task that runs the agent and delivers responses.
        """
        return asyncio.create_task(self._process(message))

    def _get_or_create_lock(self, thread_id: str) -> asyncio.Lock:
        """Return the lock for *thread_id*, creating and caching it if needed.

        When the cache reaches *_MAX_THREAD_LOCKS* entries the least-recently-used
        entry that is not currently held is evicted to prevent unbounded growth.
        """
        if thread_id in self._thread_locks:
            self._thread_locks.move_to_end(thread_id)
            return self._thread_locks[thread_id]

        lock = asyncio.Lock()
        self._thread_locks[thread_id] = lock

        if len(self._thread_locks) > _MAX_THREAD_LOCKS:
            # Evict the oldest entry that is not currently held.
            for tid, candidate in self._thread_locks.items():
                if not candidate.locked():
                    del self._thread_locks[tid]
                    break

        return lock

    async def _process(self, message: InboundMessage) -> None:
        """Run the agent for *message*, serialised per thread_id."""
        lock = self._get_or_create_lock(message.thread_id)
        async with lock:
            adapter = self._adapters.get(message.adapter_id)
            if adapter is None:
                logger.error(
                    "No adapter registered for id '%s'; dropping message.",
                    message.adapter_id,
                )
                return
            try:
                async for outbound in self._agent_service.run(message):
                    await adapter.send(outbound)
            except Exception as exc:  # noqa: BLE001
                # AgentService already yields error messages, but guard
                # against anything that escapes the generator.
                logger.error("Unhandled error in _process: %s", exc)

    # ------------------------------------------------------------------
    # Outbound path  (agent-initiated → adapter)
    # ------------------------------------------------------------------

    async def send_to(self, message: OutboundMessage) -> None:
        """Deliver *message* directly via the named adapter.

        Used for agent-initiated messages (heartbeat output, notifications)
        that do not go through the agent pipeline.

        Parameters
        ----------
        message:
            The message to deliver.  ``message.adapter_id`` must match a
            registered adapter.
        """
        adapter = self._adapters.get(message.adapter_id)
        if adapter is None:
            logger.error(
                "send_to: no adapter registered for id '%s'; dropping message.",
                message.adapter_id,
            )
            return
        await adapter.send(message)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all registered adapters concurrently and wait for all to finish.

        Each adapter's :meth:`~agent.router.base_adapter.BaseAdapter.start`
        method is launched as a concurrent task.  This coroutine returns when
        *all* adapters have exited (or raises if any raises).
        """
        if not self._adapters:
            logger.warning("MessageRouter.run() called with no registered adapters.")
            return
        await asyncio.gather(
            *(adapter.start(self) for adapter in self._adapters.values())
        )
