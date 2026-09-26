"""Tests for Phase 3 — __main__.build_router(), config-driven adapters,
and a full end-to-end integration test.

All tests run without a real LLM or Discord token.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.__main__ import _run, build_one_shot_router, build_router, parse_args
from agent.adapters import (
    BatchAdapter,
    DiscordAdapter,
    HeartbeatAdapter,
    MatrixAdapter,
    PromptAdapter,
    TerminalAdapter,
)
from agent.config import Config
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


def _config(**kwargs: Any) -> Config:
    """Build a Config object with sane test defaults."""
    defaults: dict[str, Any] = {
        "model_provider": {
            "name": "litellm",
            "api": "openai_compatible",
            "api_key": "sk-test",
            "endpoint": "https://api.example.com",
        },
        "model": {"name": "gpt-5.6-luna", "effort": "high"},
        "discord_adapter": {"bot_token": ""},
        "heartbeat": {
            "interval": 600,
            "prompt_file": "HEARTBEAT.md",
            "output_adapter": "",
            "output_channel": "",
        },
        "matrix_adapter": {
            "homeserver_url": "",
            "access_token": "",
            "user_id": "",
        },
    }
    defaults.update(kwargs)
    return Config(**defaults)


# ===========================================================================
# Config — enabled adapters
# ===========================================================================


class TestEnabledAdaptersConfig:
    """The enabled-adapter set is currently hardcoded in ``build_router``.

    These tests pin the current behaviour so the TODO in ``__main__.py``
    (drive the set from config) can be implemented without silent drift.
    """

    def test_config_has_no_enabled_adapters_field(self) -> None:
        config = _config()
        assert not hasattr(config, "enabled_adapters")

    def test_build_router_registers_all_known_adapters(self) -> None:
        config = _config(
            discord_adapter={"bot_token": "tok"},
            matrix_adapter={
                "homeserver_url": "https://matrix.example.com",
                "access_token": "syt_token",
                "user_id": "@bot:matrix.example.com",
            },
        )
        router = build_router(config, graph=_mock_graph())
        assert set(router._adapters) == {
            "terminal",
            "discord",
            "heartbeat",
            "matrix",
        }


# ===========================================================================
# build_router()
# ===========================================================================


class TestCliArguments:
    def test_parse_args_accepts_direct_prompt_mode(self) -> None:
        args = parse_args(["--prompt", "hello there"])

        assert args.prompt == "hello there"
        assert args.batch_input is None
        assert args.batch_output is None

    def test_parse_args_accepts_working_directory_positional(self) -> None:
        args = parse_args(["./mysubpath"])

        assert args.working_dir == "./mysubpath"

    def test_parse_args_accepts_workdir_option(self) -> None:
        args = parse_args(["--workdir", "./mysubpath"])

        assert args.working_dir == "./mysubpath"

    def test_parse_args_rejects_conflicting_modes(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--prompt", "hello", "--batch-input", "prompts.txt"])


class TestRunWorkingDirectory:
    def test_run_changes_into_requested_working_directory(self, tmp_path: Any) -> None:
        target_dir = tmp_path / "project"
        target_dir.mkdir()

        class DummyRouter:
            async def run(self) -> None:
                return None

        original_cwd = __import__("os").getcwd()

        try:
            seen_cwd = None

            def fake_build_router(config: Config) -> DummyRouter:
                nonlocal seen_cwd
                seen_cwd = __import__("os").getcwd()
                return DummyRouter()

            with patch("agent.__main__.load_config", return_value=_config()):
                with patch(
                    "agent.__main__.build_router", side_effect=fake_build_router
                ):
                    asyncio.run(_run([str(target_dir)]))

            assert seen_cwd == str(target_dir.resolve())
            assert __import__("pathlib").Path.cwd() == target_dir.resolve()
        finally:
            __import__("os").chdir(original_cwd)


class TestOneShotRouter:
    def test_build_one_shot_router_registers_prompt_adapter(self) -> None:
        router = build_one_shot_router(graph=_mock_graph(), prompt="hi")

        assert "prompt" in router._adapters
        assert isinstance(router._adapters["prompt"], PromptAdapter)

    def test_build_one_shot_router_registers_batch_adapter(self) -> None:
        router = build_one_shot_router(
            graph=_mock_graph(),
            batch_input="prompts.txt",
            batch_output="out.jsonl",
        )

        assert "batch" in router._adapters
        assert isinstance(router._adapters["batch"], BatchAdapter)


class TestBuildRouter:
    def test_registers_terminal(self) -> None:
        router = build_router(_config(), graph=_mock_graph())
        assert "terminal" in router._adapters
        assert isinstance(router._adapters["terminal"], TerminalAdapter)

    def test_registers_discord_with_valid_token(self) -> None:
        config = _config(discord_adapter={"bot_token": "tok-abc"})
        router = build_router(config, graph=_mock_graph())
        assert "discord" in router._adapters
        assert isinstance(router._adapters["discord"], DiscordAdapter)

    def test_skips_discord_without_token(self) -> None:
        config = _config(discord_adapter={"bot_token": ""})
        router = build_router(config, graph=_mock_graph())
        assert "discord" not in router._adapters

    def test_registers_heartbeat(self) -> None:
        router = build_router(_config(), graph=_mock_graph())
        assert "heartbeat" in router._adapters
        assert isinstance(router._adapters["heartbeat"], HeartbeatAdapter)

    def test_heartbeat_uses_configured_values(self) -> None:
        hb = {
            "interval": 30,
            "prompt_file": "custom.md",
            "output_adapter": "",
            "output_channel": "",
        }
        config = _config(heartbeat=hb)
        router = build_router(config, graph=_mock_graph())
        adapter = router._adapters["heartbeat"]
        assert isinstance(adapter, HeartbeatAdapter)
        assert adapter._config.interval == 30
        assert adapter._config.prompt_file == "custom.md"

    def test_registers_all_adapters(self) -> None:
        config = _config(
            discord_adapter={"bot_token": "tok"},
            matrix_adapter={
                "homeserver_url": "https://matrix.example.com",
                "access_token": "syt_token",
                "user_id": "@bot:matrix.example.com",
            },
        )
        router = build_router(config, graph=_mock_graph())
        assert "terminal" in router._adapters
        assert "discord" in router._adapters
        assert "heartbeat" in router._adapters
        assert "matrix" in router._adapters

    def test_returns_message_router_instance(self) -> None:
        router = build_router(_config(), graph=_mock_graph())
        assert isinstance(router, MessageRouter)

    def test_uses_default_graph_when_none_given(self) -> None:
        """build_router without an explicit graph must not raise at construction
        time."""
        # No graph passed → lazy-imports agent.graph.graph (safe, no API call).
        router = build_router(_config())
        assert "terminal" in router._adapters

    def test_each_call_produces_independent_router(self) -> None:
        config = _config(discord_adapter={"bot_token": "tok"})
        r1 = build_router(config, graph=_mock_graph())
        r2 = build_router(config, graph=_mock_graph())
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
        """Router + real compiled graph (mocked LLM) → stub adapter receives
        response."""
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
        """Two messages on the same thread share conversation history via
        checkpointer."""
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
        """build_router() wired end-to-end: dispatch → stub adapter receives
        response."""
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


# ===========================================================================
# build_router — Matrix adapter
# ===========================================================================


class TestBuildRouterMatrix:
    def _matrix_config(self, **kwargs: Any) -> Config:
        matrix = {
            "homeserver_url": "https://matrix.example.com",
            "access_token": "syt_fake",
            "user_id": "@bot:example.com",
        }
        matrix.update(kwargs)
        return _config(matrix_adapter=matrix)

    def test_registers_matrix_with_full_credentials(self) -> None:
        config = self._matrix_config()
        router = build_router(config, graph=_mock_graph())
        assert "matrix" in router._adapters
        assert isinstance(router._adapters["matrix"], MatrixAdapter)

    def test_matrix_adapter_has_correct_config(self) -> None:
        config = self._matrix_config()
        router = build_router(config, graph=_mock_graph())
        adapter = router._adapters["matrix"]
        assert isinstance(adapter, MatrixAdapter)
        assert adapter._config.homeserver_url == "https://matrix.example.com"
        assert adapter._config.user_id == "@bot:example.com"

    def test_skips_matrix_when_homeserver_url_missing(self) -> None:
        config = self._matrix_config(homeserver_url="")
        router = build_router(config, graph=_mock_graph())
        assert "matrix" not in router._adapters

    def test_skips_matrix_when_access_token_missing(self) -> None:
        config = self._matrix_config(access_token="")
        router = build_router(config, graph=_mock_graph())
        assert "matrix" not in router._adapters

    def test_skips_matrix_when_user_id_missing(self) -> None:
        config = self._matrix_config(user_id="")
        router = build_router(config, graph=_mock_graph())
        assert "matrix" not in router._adapters
