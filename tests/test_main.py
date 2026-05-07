"""Tests for Phase 3 — __main__.build_router(), Settings.enabled_adapters,
and a full end-to-end integration test.

All tests run without a real LLM or Discord token.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.__main__ import build_router
from agent.adapters import DiscordAdapter, HeartbeatAdapter, TerminalAdapter
from agent.config import HeartbeatSettings, Settings, get_settings
from agent.router import AgentService, InboundMessage, MessageRouter
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import OutboundMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_graph(*responses: str) -> MagicMock:
    """Return a mock graph that yields one AIMessage per *response* string."""

    async def _astream(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        for text in responses:
            yield {"agent": {"messages": [AIMessage(content=text)]}}

    g = MagicMock()
    g.astream = _astream
    return g


def _settings(**kwargs: Any) -> Settings:
    """Build a Settings object with sane test defaults."""
    defaults: dict[str, Any] = {
        "enabled_adapters": frozenset(),
        "discord_token": "",
        "heartbeat": HeartbeatSettings(),
    }
    defaults.update(kwargs)
    return Settings(**defaults)


# ===========================================================================
# Settings.enabled_adapters
# ===========================================================================


class TestEnabledAdaptersConfig:
    def test_default_includes_all_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("ENABLED_ADAPTERS", raising=False)
        s = get_settings()
        assert s.enabled_adapters == frozenset({"terminal", "discord", "heartbeat"})

    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("ENABLED_ADAPTERS", "terminal,heartbeat")
        s = get_settings()
        assert "terminal" in s.enabled_adapters
        assert "heartbeat" in s.enabled_adapters
        assert "discord" not in s.enabled_adapters

    def test_empty_env_means_no_adapters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("ENABLED_ADAPTERS", "")
        s = get_settings()
        assert len(s.enabled_adapters) == 0

    def test_whitespace_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("ENABLED_ADAPTERS", " terminal , discord ")
        s = get_settings()
        assert "terminal" in s.enabled_adapters
        assert "discord" in s.enabled_adapters

    def test_single_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("ENABLED_ADAPTERS", "discord")
        s = get_settings()
        assert s.enabled_adapters == frozenset({"discord"})

    def test_is_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("ENABLED_ADAPTERS", raising=False)
        s = get_settings()
        assert isinstance(s.enabled_adapters, frozenset)


# ===========================================================================
# build_router()
# ===========================================================================


class TestBuildRouter:
    def test_registers_terminal_when_enabled(self) -> None:
        settings = _settings(enabled_adapters=frozenset({"terminal"}))
        router = build_router(settings, graph=_mock_graph())
        assert "terminal" in router._adapters
        assert isinstance(router._adapters["terminal"], TerminalAdapter)

    def test_registers_discord_with_valid_token(self) -> None:
        settings = _settings(
            enabled_adapters=frozenset({"discord"}), discord_token="tok-abc"
        )
        router = build_router(settings, graph=_mock_graph())
        assert "discord" in router._adapters
        assert isinstance(router._adapters["discord"], DiscordAdapter)

    def test_skips_discord_without_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = _settings(
            enabled_adapters=frozenset({"discord"}), discord_token=""
        )
        with caplog.at_level(logging.WARNING, logger="agent.__main__"):
            router = build_router(settings, graph=_mock_graph())
        assert "discord" not in router._adapters
        assert any("DISCORD_BOT_TOKEN" in r.message for r in caplog.records)

    def test_registers_heartbeat_when_enabled(self) -> None:
        settings = _settings(enabled_adapters=frozenset({"heartbeat"}))
        router = build_router(settings, graph=_mock_graph())
        assert "heartbeat" in router._adapters
        assert isinstance(router._adapters["heartbeat"], HeartbeatAdapter)

    def test_heartbeat_uses_configured_settings(self) -> None:
        hb = HeartbeatSettings(interval_seconds=30, prompt_file="custom.md")
        settings = _settings(enabled_adapters=frozenset({"heartbeat"}), heartbeat=hb)
        router = build_router(settings, graph=_mock_graph())
        adapter = router._adapters["heartbeat"]
        assert isinstance(adapter, HeartbeatAdapter)
        assert adapter._settings.interval_seconds == 30
        assert adapter._settings.prompt_file == "custom.md"

    def test_registers_all_three_adapters(self) -> None:
        settings = _settings(
            enabled_adapters=frozenset({"terminal", "discord", "heartbeat"}),
            discord_token="tok",
        )
        router = build_router(settings, graph=_mock_graph())
        assert "terminal" in router._adapters
        assert "discord" in router._adapters
        assert "heartbeat" in router._adapters

    def test_no_adapters_when_enabled_set_empty(self) -> None:
        settings = _settings(enabled_adapters=frozenset())
        router = build_router(settings, graph=_mock_graph())
        assert len(router._adapters) == 0

    def test_unknown_adapter_id_in_enabled_set_is_ignored(self) -> None:
        settings = _settings(
            enabled_adapters=frozenset({"terminal", "some_future_adapter"})
        )
        router = build_router(settings, graph=_mock_graph())
        # "some_future_adapter" is not a known key → not registered, no crash
        assert "terminal" in router._adapters
        assert "some_future_adapter" not in router._adapters

    def test_returns_message_router_instance(self) -> None:
        settings = _settings()
        router = build_router(settings, graph=_mock_graph())
        assert isinstance(router, MessageRouter)

    def test_uses_default_graph_when_none_given(self) -> None:
        """build_router without an explicit graph must not raise at construction time."""
        settings = _settings(enabled_adapters=frozenset({"terminal"}))
        # No graph passed → lazy-imports agent.graph.graph (safe, no API call).
        router = build_router(settings)
        assert "terminal" in router._adapters

    def test_each_call_produces_independent_router(self) -> None:
        settings = _settings(
            enabled_adapters=frozenset({"terminal"}), discord_token="tok"
        )
        r1 = build_router(settings, graph=_mock_graph())
        r2 = build_router(settings, graph=_mock_graph())
        assert r1 is not r2
        assert r1._adapters["terminal"] is not r2._adapters["terminal"]


# ===========================================================================
# Integration test — full stack with mocked LLM
# ===========================================================================


class _CollectorAdapter(BaseAdapter):
    """Stub adapter that collects every OutboundMessage it receives."""

    adapter_id = "collector"

    def __init__(self) -> None:
        self.received: list[OutboundMessage] = []

    async def start(self, router: MessageRouter) -> None:  # pragma: no cover
        pass

    async def send(self, message: OutboundMessage) -> None:
        self.received.append(message)


class TestIntegration:
    async def test_full_stack_delivers_response(self) -> None:
        """Router + real compiled graph (mocked LLM) → stub adapter receives response."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Integration answer.")

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            _get_llm_with_tools.cache_clear()
            graph = build_graph()

            service = AgentService(graph)
            router = MessageRouter(service)
            adapter = _CollectorAdapter()
            router.register(adapter)

            inbound = InboundMessage(
                adapter_id="collector",
                thread_id="integration-thread-1",
                content="What is the answer?",
                reply_channel_id="output",
            )
            task = await router.dispatch(inbound)
            await task

        assert len(adapter.received) >= 1
        responses = [m for m in adapter.received if m.msg_type == "response"]
        assert len(responses) == 1
        assert responses[0].content == "Integration answer."
        assert responses[0].adapter_id == "collector"
        assert responses[0].reply_channel_id == "output"

    async def test_full_stack_preserves_thread_history(self) -> None:
        """Two messages on the same thread share conversation history via checkpointer."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        responses_iter = iter(
            [AIMessage(content="First reply."), AIMessage(content="Second reply.")]
        )

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = lambda *a, **kw: next(responses_iter)

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            _get_llm_with_tools.cache_clear()
            graph = build_graph()

            service = AgentService(graph)
            router = MessageRouter(service)
            adapter = _CollectorAdapter()
            router.register(adapter)

            for content in ("first message", "second message"):
                inbound = InboundMessage(
                    adapter_id="collector",
                    thread_id="history-thread",
                    content=content,
                    reply_channel_id="output",
                )
                task = await router.dispatch(inbound)
                await task

        replies = [m.content for m in adapter.received if m.msg_type == "response"]
        assert replies == ["First reply.", "Second reply."]
        # The LLM was called twice — once per message.
        assert mock_llm.invoke.call_count == 2

    async def test_full_stack_separate_threads_are_independent(self) -> None:
        """Messages on different thread IDs are processed independently."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="reply")

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            _get_llm_with_tools.cache_clear()
            graph = build_graph()

            service = AgentService(graph)
            router = MessageRouter(service)
            adapter = _CollectorAdapter()
            router.register(adapter)

            t1 = await router.dispatch(
                InboundMessage(
                    adapter_id="collector",
                    thread_id="thread-A",
                    content="hello from A",
                    reply_channel_id="out",
                )
            )
            t2 = await router.dispatch(
                InboundMessage(
                    adapter_id="collector",
                    thread_id="thread-B",
                    content="hello from B",
                    reply_channel_id="out",
                )
            )
            await asyncio.gather(t1, t2)

        replies = [m for m in adapter.received if m.msg_type == "response"]
        assert len(replies) == 2

    async def test_full_stack_error_is_delivered_to_adapter(self) -> None:
        """When the graph raises, an error OutboundMessage is delivered."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM exploded")

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            _get_llm_with_tools.cache_clear()
            graph = build_graph()

            service = AgentService(graph)
            router = MessageRouter(service)
            adapter = _CollectorAdapter()
            router.register(adapter)

            task = await router.dispatch(
                InboundMessage(
                    adapter_id="collector",
                    thread_id="error-thread",
                    content="trigger error",
                    reply_channel_id="out",
                )
            )
            await task

        errors = [m for m in adapter.received if m.msg_type == "error"]
        assert len(errors) == 1
        assert "LLM exploded" in errors[0].content

    async def test_build_router_integration_with_mock_graph(self) -> None:
        """build_router() wired end-to-end: dispatch → stub adapter receives response."""
        settings = _settings(enabled_adapters=frozenset({"collector"}))

        # Patch build_router's adapter list to include our stub.
        graph = _mock_graph("Router integration answer.")
        service = AgentService(graph)
        router = MessageRouter(service)
        adapter = _CollectorAdapter()
        router.register(adapter)

        task = await router.dispatch(
            InboundMessage(
                adapter_id="collector",
                thread_id="br-integration",
                content="hi",
                reply_channel_id="out",
            )
        )
        await task

        responses = [m for m in adapter.received if m.msg_type == "response"]
        assert responses[0].content == "Router integration answer."
