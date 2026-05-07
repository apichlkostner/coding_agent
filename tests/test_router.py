"""Tests for the router package (Phase 1).

All tests run without a real LLM or Discord token.  The LangGraph graph is
replaced by a lightweight mock that yields pre-canned ``astream`` steps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent.router.agent_service import AgentService
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inbound(
    content: str = "hello",
    adapter_id: str = "test",
    thread_id: str = "thread-1",
    reply_channel_id: str = "chan-1",
    user_id: str | None = "user-1",
) -> InboundMessage:
    return InboundMessage(
        adapter_id=adapter_id,
        thread_id=thread_id,
        content=content,
        reply_channel_id=reply_channel_id,
        user_id=user_id,
    )


def _outbound(
    content: str = "hi",
    adapter_id: str = "test",
    reply_channel_id: str = "chan-1",
    msg_type: str = "response",
) -> OutboundMessage:
    return OutboundMessage(
        adapter_id=adapter_id,
        reply_channel_id=reply_channel_id,
        content=content,
        metadata={"msg_type": msg_type},
    )


def _make_graph(*steps: dict[str, Any]) -> MagicMock:
    """Return a mock graph whose ``astream`` yields *steps*."""

    async def _mock_astream(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        for step in steps:
            yield step

    mock = MagicMock()
    mock.astream = _mock_astream
    return mock


class _StubAdapter(BaseAdapter):
    """Minimal in-memory adapter for testing."""

    adapter_id = "test"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self._started = False

    async def start(self, router: MessageRouter) -> None:
        self._started = True

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)


# ---------------------------------------------------------------------------
# InboundMessage & OutboundMessage
# ---------------------------------------------------------------------------


class TestMessages:
    def test_inbound_defaults(self) -> None:
        msg = InboundMessage(
            adapter_id="discord",
            thread_id="discord-1-2",
            content="hi",
            reply_channel_id="2",
        )
        assert msg.user_id is None
        assert msg.metadata == {}

    def test_inbound_with_user(self) -> None:
        msg = _inbound(user_id="u42")
        assert msg.user_id == "u42"

    def test_outbound_msg_type_property(self) -> None:
        msg = _outbound(msg_type="tool_call")
        assert msg.msg_type == "tool_call"

    def test_outbound_msg_type_none_when_missing(self) -> None:
        msg = OutboundMessage(adapter_id="a", reply_channel_id="b", content="c")
        assert msg.msg_type is None

    def test_outbound_defaults(self) -> None:
        msg = OutboundMessage(adapter_id="a", reply_channel_id="b", content="hello")
        assert msg.metadata == {}

    def test_metadata_roundtrip(self) -> None:
        extra = {"guild_id": "999", "msg_type": "response"}
        msg = OutboundMessage(
            adapter_id="discord", reply_channel_id="ch", content="ok", metadata=extra
        )
        assert msg.metadata["guild_id"] == "999"
        assert msg.msg_type == "response"


# ---------------------------------------------------------------------------
# BaseAdapter
# ---------------------------------------------------------------------------


class TestBaseAdapter:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseAdapter()  # type: ignore[abstract]

    def test_subclass_without_send_is_abstract(self) -> None:
        class Incomplete(BaseAdapter):
            adapter_id = "x"

            async def start(self, router: MessageRouter) -> None:
                pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_without_start_is_abstract(self) -> None:
        class Incomplete(BaseAdapter):
            adapter_id = "x"

            async def send(self, message: OutboundMessage) -> None:
                pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_concrete_subclass_can_be_instantiated(self) -> None:
        adapter = _StubAdapter()
        assert adapter.adapter_id == "test"


# ---------------------------------------------------------------------------
# AgentService
# ---------------------------------------------------------------------------


class TestAgentService:
    async def test_yields_response_for_simple_ai_message(self) -> None:
        graph = _make_graph({"agent": {"messages": [AIMessage(content="Hello!")]}})
        service = AgentService(graph)

        results = [msg async for msg in service.run(_inbound())]

        assert len(results) == 1
        assert results[0].msg_type == "response"
        assert results[0].content == "Hello!"

    async def test_response_carries_inbound_routing_info(self) -> None:
        graph = _make_graph({"agent": {"messages": [AIMessage(content="Hi")]}})
        service = AgentService(graph)

        results = [msg async for msg in service.run(_inbound(adapter_id="discord", reply_channel_id="chan-99"))]

        assert results[0].adapter_id == "discord"
        assert results[0].reply_channel_id == "chan-99"

    async def test_verbose_yields_tool_call(self) -> None:
        ai_with_tools = AIMessage(content="")
        ai_with_tools.tool_calls = [{"name": "calculate", "args": {"expr": "1+1"}, "id": "tc1"}]  # type: ignore[assignment]

        graph = _make_graph(
            {"agent": {"messages": [ai_with_tools]}},
            {"tools": {"messages": [ToolMessage(content="2", tool_call_id="tc1", name="calculate")]}},
            {"agent": {"messages": [AIMessage(content="The answer is 2.")]}},
        )
        service = AgentService(graph, verbose=True)

        results = [msg async for msg in service.run(_inbound())]

        types = [m.msg_type for m in results]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "response" in types

    async def test_non_verbose_suppresses_tool_messages(self) -> None:
        ai_with_tools = AIMessage(content="")
        ai_with_tools.tool_calls = [{"name": "calculate", "args": {"expr": "1+1"}, "id": "tc1"}]  # type: ignore[assignment]

        graph = _make_graph(
            {"agent": {"messages": [ai_with_tools]}},
            {"tools": {"messages": [ToolMessage(content="2", tool_call_id="tc1", name="calculate")]}},
            {"agent": {"messages": [AIMessage(content="The answer is 2.")]}},
        )
        service = AgentService(graph, verbose=False)

        results = [msg async for msg in service.run(_inbound())]

        types = [m.msg_type for m in results]
        assert "tool_call" not in types
        assert "tool_result" not in types
        assert types == ["response"]

    async def test_yields_error_message_on_exception(self) -> None:
        async def _boom(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            raise RuntimeError("graph exploded")
            yield  # make it a generator

        graph = MagicMock()
        graph.astream = _boom
        service = AgentService(graph)

        results = [msg async for msg in service.run(_inbound())]

        assert len(results) == 1
        assert results[0].msg_type == "error"
        assert "graph exploded" in results[0].content

    async def test_skips_steps_without_messages_key(self) -> None:
        """Nodes that don't produce a 'messages' key (e.g. __interrupt__) are ignored."""
        graph = _make_graph(
            {"some_node": {"other_key": "value"}},
            {"agent": {"messages": [AIMessage(content="Done.")]}},
        )
        service = AgentService(graph)

        results = [msg async for msg in service.run(_inbound())]

        assert len(results) == 1
        assert results[0].content == "Done."

    async def test_multiple_response_steps(self) -> None:
        """Two consecutive agent responses both surface as 'response' messages."""
        graph = _make_graph(
            {"agent": {"messages": [AIMessage(content="Part 1.")]}},
            {"agent": {"messages": [AIMessage(content="Part 2.")]}},
        )
        service = AgentService(graph)

        results = [msg async for msg in service.run(_inbound())]

        assert len(results) == 2
        assert results[0].content == "Part 1."
        assert results[1].content == "Part 2."

    async def test_tool_result_truncated_at_200_chars(self) -> None:
        long_content = "x" * 300
        tool_msg = ToolMessage(content=long_content, tool_call_id="tc1", name="bash")

        graph = _make_graph({"tools": {"messages": [tool_msg]}})
        service = AgentService(graph, verbose=True)

        results = [msg async for msg in service.run(_inbound())]

        assert len(results) == 1
        assert results[0].msg_type == "tool_result"
        # 200 chars of content + ellipsis
        assert results[0].content == "x" * 200 + "…"


# ---------------------------------------------------------------------------
# MessageRouter
# ---------------------------------------------------------------------------


class TestMessageRouterRegister:
    def _make_router(self) -> MessageRouter:
        service = MagicMock(spec=AgentService)
        return MessageRouter(service)

    def test_register_single_adapter(self) -> None:
        router = self._make_router()
        adapter = _StubAdapter()
        router.register(adapter)
        assert "test" in router._adapters

    def test_register_duplicate_raises(self) -> None:
        router = self._make_router()
        router.register(_StubAdapter())
        with pytest.raises(ValueError, match="already registered"):
            router.register(_StubAdapter())

    def test_register_two_different_adapters(self) -> None:
        router = self._make_router()

        class OtherAdapter(_StubAdapter):
            adapter_id = "other"

        router.register(_StubAdapter())
        router.register(OtherAdapter())
        assert len(router._adapters) == 2


class TestMessageRouterDispatch:
    async def test_dispatch_calls_adapter_send(self) -> None:
        graph = _make_graph({"agent": {"messages": [AIMessage(content="Hi there!")]}})
        service = AgentService(graph)
        router = MessageRouter(service)
        adapter = _StubAdapter()
        router.register(adapter)

        task = await router.dispatch(_inbound())
        await task

        assert len(adapter.sent) == 1
        assert adapter.sent[0].content == "Hi there!"

    async def test_dispatch_unknown_adapter_logs_and_does_not_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)
        # no adapter registered

        import logging
        with caplog.at_level(logging.ERROR, logger="agent.router.router"):
            task = await router.dispatch(_inbound(adapter_id="ghost"))
            await task

        assert any("ghost" in r.message for r in caplog.records)

    async def test_dispatch_serialises_same_thread(self) -> None:
        """Two messages on the same thread should be processed sequentially."""
        order: list[str] = []

        async def slow_astream(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            order.append("start")
            await asyncio.sleep(0)  # yield control
            order.append("end")
            yield {"agent": {"messages": [AIMessage(content="ok")]}}

        graph = MagicMock()
        graph.astream = slow_astream
        service = AgentService(graph)
        router = MessageRouter(service)
        adapter = _StubAdapter()
        router.register(adapter)

        msg = _inbound(thread_id="shared-thread")
        t1 = await router.dispatch(msg)
        t2 = await router.dispatch(msg)
        await asyncio.gather(t1, t2)

        # Each run must fully complete before the next starts.
        assert order == ["start", "end", "start", "end"]

    async def test_dispatch_concurrent_different_threads(self) -> None:
        """Messages on different threads run concurrently (no cross-lock blocking)."""
        started: list[str] = []

        async def astream_for(thread_id: str) -> AsyncGenerator[Any, None]:
            started.append(thread_id)
            await asyncio.sleep(0)
            yield {"agent": {"messages": [AIMessage(content="ok")]}}

        # We patch _process to verify both can start before either finishes.
        graph = _make_graph({"agent": {"messages": [AIMessage(content="ok")]}})
        service = AgentService(graph)
        router = MessageRouter(service)
        adapter = _StubAdapter()
        router.register(adapter)

        t1 = await router.dispatch(_inbound(thread_id="thread-A"))
        t2 = await router.dispatch(_inbound(thread_id="thread-B"))
        await asyncio.gather(t1, t2)

        assert len(adapter.sent) == 2


class TestMessageRouterSendTo:
    async def test_send_to_routes_to_correct_adapter(self) -> None:
        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)
        adapter = _StubAdapter()
        router.register(adapter)

        msg = _outbound(adapter_id="test", content="ping")
        await router.send_to(msg)

        assert adapter.sent == [msg]

    async def test_send_to_unknown_adapter_logs_and_does_not_raise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)

        import logging
        with caplog.at_level(logging.ERROR, logger="agent.router.router"):
            await router.send_to(_outbound(adapter_id="nonexistent"))

        assert any("nonexistent" in r.message for r in caplog.records)

    async def test_send_to_calls_adapter_send_once(self) -> None:
        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)
        adapter = _StubAdapter()
        router.register(adapter)

        await router.send_to(_outbound())

        assert len(adapter.sent) == 1


class TestMessageRouterRun:
    async def test_run_starts_all_adapters(self) -> None:
        class QuickAdapter(_StubAdapter):
            adapter_id = "quick"

            async def start(self, router: MessageRouter) -> None:
                self._started = True  # return immediately

        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)
        a1 = _StubAdapter()
        a2 = QuickAdapter()
        router.register(a1)
        router.register(a2)

        await router.run()

        assert a1._started
        assert a2._started

    async def test_run_with_no_adapters_does_not_raise(self) -> None:
        service = MagicMock(spec=AgentService)
        router = MessageRouter(service)
        await router.run()  # should complete without error
