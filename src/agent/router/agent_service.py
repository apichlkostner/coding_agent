"""Agent service — the single place that talks to the LangGraph graph.

All stream-processing logic (step parsing, message classification, error
handling) lives here.  Adapters never touch the graph directly.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from agent.router.messages import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class AgentService:
    """Wraps the LangGraph graph and streams :class:`OutboundMessage` objects.

    Parameters
    ----------
    graph:
        A compiled LangGraph ``StateGraph``.  The module-level singleton from
        ``agent.graph`` is the normal value; tests may pass a mock.
    verbose:
        When ``True`` (default), ``tool_call`` and ``tool_result`` messages
        are yielded in addition to the final ``response``.  Set to ``False``
        to surface only the agent's final reply.
    """

    def __init__(self, graph: CompiledStateGraph, *, verbose: bool = True) -> None:
        self._graph = graph
        self._verbose = verbose

    async def run(
        self, message: InboundMessage
    ) -> AsyncGenerator[OutboundMessage, None]:
        """Stream agent outputs for *message*, yielding one :class:`OutboundMessage`
        per meaningful event.

        Yields
        ------
        OutboundMessage
            - ``msg_type="tool_call"`` — agent requested tool(s) (verbose only).
            - ``msg_type="tool_result"`` — tool execution result (verbose only).
            - ``msg_type="response"`` — final agent text response.
            - ``msg_type="error"`` — unrecoverable error during processing.

        The ``adapter_id`` and ``reply_channel_id`` on every yielded message
        are copied from *message* so the router knows where to send it.
        """
        config = {"configurable": {"thread_id": message.thread_id}}

        def _make(content: str, msg_type: str, node_name: str = "") -> OutboundMessage:
            return OutboundMessage(
                adapter_id=message.adapter_id,
                reply_channel_id=message.reply_channel_id,
                content=content,
                metadata={"msg_type": msg_type, "node_name": node_name},
            )

        try:
            async for step in self._graph.astream(
                {"messages": [HumanMessage(content=message.content)]},
                stream_mode="updates",
                config=config,
            ):
                node_name, node_output = next(iter(step.items()))

                # Some internal nodes (e.g. __interrupt__) don't carry messages.
                if "messages" not in node_output:
                    continue

                last_msg = node_output["messages"][-1]

                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    calls = ", ".join(
                        tc["name"] + "(" + str(tc["args"])[:50] + ")"
                        for tc in last_msg.tool_calls
                    )
                    logger.info("[%s] tool calls: %s", node_name, calls)
                    if self._verbose:
                        yield _make(calls, "tool_call", node_name)

                elif hasattr(last_msg, "name") and last_msg.name is not None:
                    # ToolMessage — execution result
                    preview = last_msg.content[:200]
                    if len(last_msg.content) > 200:
                        preview += "…"
                    logger.info("[%s] tool result: %s", node_name, preview)
                    if self._verbose:
                        yield _make(preview, "tool_result", node_name)

                else:
                    # Final agent response
                    logger.info(
                        "[%s] response: %s",
                        node_name,
                        last_msg.content[:100],
                    )
                    yield _make(last_msg.content, "response", node_name)

        except Exception as exc:  # noqa: BLE001
            logger.error("AgentService error for thread '%s': %s", message.thread_id, exc)
            yield _make(f"Error: {exc!s:.200}", "error")
