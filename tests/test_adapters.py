"""Tests for Phase 2 — adapters and related config.

All tests run without a real LLM, Discord token, or interactive terminal.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.adapters.discord_adapter import DiscordAdapter, _DiscordClient
from agent.adapters.heartbeat_adapter import HeartbeatAdapter
from agent.adapters.terminal_adapter import TerminalAdapter
from agent.config import HeartbeatSettings, Settings, get_settings
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _outbound(
    content: str = "hello",
    adapter_id: str = "terminal",
    reply_channel_id: str = "stdout",
    msg_type: str = "response",
    node_name: str = "agent",
) -> OutboundMessage:
    return OutboundMessage(
        adapter_id=adapter_id,
        reply_channel_id=reply_channel_id,
        content=content,
        metadata={"msg_type": msg_type, "node_name": node_name},
    )


def _async_cm() -> MagicMock:
    """Mock async context manager (for channel.typing())."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_router_with_dispatch() -> tuple[MagicMock, list[InboundMessage]]:
    """Return a mock router and the list that collects dispatched messages."""
    captured: list[InboundMessage] = []

    async def _noop() -> None:
        pass

    async def fake_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
        captured.append(msg)
        return asyncio.create_task(_noop())

    router = MagicMock()
    router.dispatch = fake_dispatch
    return router, captured


# ===========================================================================
# Config — HeartbeatSettings
# ===========================================================================


class TestHeartbeatSettings:
    def test_defaults(self) -> None:
        s = HeartbeatSettings()
        assert s.interval_seconds == 600
        assert s.prompt_file == "HEARTBEAT.md"

    def test_custom_values(self) -> None:
        s = HeartbeatSettings(interval_seconds=60, prompt_file="custom.md")
        assert s.interval_seconds == 60
        assert s.prompt_file == "custom.md"

    def test_settings_includes_heartbeat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        s = get_settings()
        assert isinstance(s.heartbeat, HeartbeatSettings)

    def test_heartbeat_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", "120")
        s = get_settings()
        assert s.heartbeat.interval_seconds == 120

    def test_heartbeat_prompt_file_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("HEARTBEAT_PROMPT_FILE", "MY_BEAT.md")
        s = get_settings()
        assert s.heartbeat.prompt_file == "MY_BEAT.md"


# ===========================================================================
# TerminalAdapter
# ===========================================================================


class TestTerminalAdapterSend:
    async def test_send_response_prints_with_node_prefix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(_outbound(content="Hello!", msg_type="response", node_name="agent"))
        assert capsys.readouterr().out.strip() == "[agent] Hello!"

    async def test_send_tool_call_prints_arrow(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(_outbound(content="calculate({'expr': '1+1'})", msg_type="tool_call", node_name="agent"))
        out = capsys.readouterr().out
        assert "→" in out
        assert "calculate" in out

    async def test_send_tool_result_prints_left_arrow(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(_outbound(content="2", msg_type="tool_result", node_name="tools"))
        out = capsys.readouterr().out
        assert "←" in out
        assert "[tools]" in out

    async def test_send_error_prints_error_prefix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(_outbound(content="something went wrong", msg_type="error"))
        assert capsys.readouterr().out.strip() == "Error: something went wrong"

    async def test_send_unknown_type_prints_raw(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        msg = OutboundMessage(
            adapter_id="terminal",
            reply_channel_id="stdout",
            content="raw content",
            metadata={"msg_type": "unknown_future_type"},
        )
        adapter = TerminalAdapter()
        await adapter.send(msg)
        assert "raw content" in capsys.readouterr().out

    async def test_send_falls_back_to_agent_when_node_name_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        msg = OutboundMessage(
            adapter_id="terminal",
            reply_channel_id="stdout",
            content="Hi",
            metadata={"msg_type": "response"},  # no node_name key
        )
        adapter = TerminalAdapter()
        await adapter.send(msg)
        assert "[agent]" in capsys.readouterr().out

    async def test_send_falls_back_to_agent_when_node_name_empty(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        msg = OutboundMessage(
            adapter_id="terminal",
            reply_channel_id="stdout",
            content="Hi",
            metadata={"msg_type": "response", "node_name": ""},
        )
        adapter = TerminalAdapter()
        await adapter.send(msg)
        assert "[agent]" in capsys.readouterr().out


class TestTerminalAdapterStart:
    async def test_start_dispatches_message(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()

        with patch.object(
            adapter._session,
            "prompt_async",
            new=AsyncMock(side_effect=["hello world", EOFError()]),
        ):
            await adapter.start(router)

        assert len(captured) == 1
        assert captured[0].content == "hello world"
        assert captured[0].adapter_id == "terminal"
        assert captured[0].thread_id == "terminal-cli"
        assert captured[0].reply_channel_id == "stdout"
        assert captured[0].user_id is None

    async def test_start_skips_empty_input(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()

        with patch.object(
            adapter._session,
            "prompt_async",
            new=AsyncMock(side_effect=["", "  ", "hi", EOFError()]),
        ):
            await adapter.start(router)

        assert len(captured) == 1
        assert captured[0].content == "hi"

    async def test_start_returns_on_quit_command(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()

        for cmd in ("quit", "Quit", "QUIT", "exit", "q"):
            captured.clear()
            with patch.object(
                adapter._session,
                "prompt_async",
                new=AsyncMock(side_effect=[cmd]),
            ):
                await adapter.start(router)
            assert len(captured) == 0, f"'{cmd}' should not dispatch"

    async def test_start_returns_on_eof(self, capsys: pytest.CaptureFixture[str]) -> None:
        adapter = TerminalAdapter()
        router, _ = _mock_router_with_dispatch()

        with patch.object(
            adapter._session, "prompt_async", new=AsyncMock(side_effect=EOFError())
        ):
            await adapter.start(router)  # must return, not raise

        assert "Goodbye" in capsys.readouterr().out

    async def test_start_reraises_keyboard_interrupt(self) -> None:
        adapter = TerminalAdapter()
        router, _ = _mock_router_with_dispatch()

        with patch.object(
            adapter._session, "prompt_async", new=AsyncMock(side_effect=KeyboardInterrupt())
        ):
            with pytest.raises(KeyboardInterrupt):
                await adapter.start(router)

    async def test_start_awaits_task_before_next_prompt(self) -> None:
        """The task must be awaited so the response is printed before the next prompt."""
        adapter = TerminalAdapter()
        order: list[str] = []

        async def slow_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            async def work() -> None:
                order.append("task_start")
                await asyncio.sleep(0)
                order.append("task_end")

            return asyncio.create_task(work())

        router = MagicMock()
        router.dispatch = slow_dispatch

        prompt_calls = 0

        async def mock_prompt(_: str) -> str:
            nonlocal prompt_calls
            prompt_calls += 1
            if prompt_calls == 1:
                order.append("prompt_1")
                return "first"
            order.append("prompt_2")
            raise EOFError()

        with patch.object(adapter._session, "prompt_async", new=mock_prompt):
            await adapter.start(router)

        # prompt_1 → task_start → task_end → prompt_2
        assert order == ["prompt_1", "task_start", "task_end", "prompt_2"]


# ===========================================================================
# DiscordAdapter
# ===========================================================================


class TestDiscordAdapterSend:
    def _adapter_with_mock_channel(
        self, channel_id: int = 99
    ) -> tuple[DiscordAdapter, AsyncMock]:
        """Return an adapter whose cached channel is a fresh AsyncMock."""
        adapter = DiscordAdapter(token="fake-token")
        mock_channel = AsyncMock()
        adapter._client.get_channel = MagicMock(return_value=mock_channel)
        return adapter, mock_channel

    async def test_send_response_calls_channel_send(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        msg = _outbound(
            content="Here is your answer.",
            adapter_id="discord",
            reply_channel_id="99",
            msg_type="response",
        )
        await adapter.send(msg)
        ch.send.assert_awaited_once_with("Here is your answer.")

    async def test_send_error_calls_channel_send(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        msg = _outbound(
            content="oops",
            adapter_id="discord",
            reply_channel_id="99",
            msg_type="error",
        )
        await adapter.send(msg)
        ch.send.assert_awaited_once()

    async def test_send_skips_tool_call(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        await adapter.send(_outbound(msg_type="tool_call", adapter_id="discord", reply_channel_id="99"))
        ch.send.assert_not_awaited()

    async def test_send_skips_tool_result(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        await adapter.send(_outbound(msg_type="tool_result", adapter_id="discord", reply_channel_id="99"))
        ch.send.assert_not_awaited()

    async def test_send_skips_empty_content(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        msg = _outbound(content="", msg_type="response", adapter_id="discord", reply_channel_id="99")
        await adapter.send(msg)
        ch.send.assert_not_awaited()

    async def test_send_chunks_long_message(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        # 4001 chars → chunks of 2000 + 2000 + 1 = 3 sends
        big = "x" * 4001
        msg = _outbound(content=big, msg_type="response", adapter_id="discord", reply_channel_id="99")
        await adapter.send(msg)
        assert ch.send.await_count == 3
        # First chunk is exactly 2000 chars.
        assert len(ch.send.call_args_list[0].args[0]) == 2000

    async def test_send_fetches_channel_when_not_cached(self) -> None:
        adapter = DiscordAdapter(token="fake-token")
        mock_channel = AsyncMock()
        # get_channel returns None → fall back to fetch_channel
        adapter._client.get_channel = MagicMock(return_value=None)
        adapter._client.fetch_channel = AsyncMock(return_value=mock_channel)

        msg = _outbound(content="hi", msg_type="response", adapter_id="discord", reply_channel_id="99")
        await adapter.send(msg)

        adapter._client.fetch_channel.assert_awaited_once_with(99)
        mock_channel.send.assert_awaited_once_with("hi")

    async def test_send_logs_error_when_channel_fetch_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = DiscordAdapter(token="fake-token")
        adapter._client.get_channel = MagicMock(return_value=None)
        adapter._client.fetch_channel = AsyncMock(side_effect=Exception("not found"))

        msg = _outbound(content="hi", msg_type="response", adapter_id="discord", reply_channel_id="99")
        with caplog.at_level(logging.ERROR, logger="agent.adapters.discord_adapter"):
            await adapter.send(msg)  # must not raise

        assert any("99" in r.message for r in caplog.records)


class TestDiscordAdapterHandleMessage:
    async def test_handle_message_builds_correct_inbound(self) -> None:
        adapter = DiscordAdapter(token="fake-token")
        router, captured = _mock_router_with_dispatch()
        adapter._router = router  # type: ignore[assignment]

        discord_msg = MagicMock()
        discord_msg.author.id = 42
        discord_msg.author.bot = False
        discord_msg.channel.id = 99
        discord_msg.content = "Hello bot!"
        discord_msg.channel.typing.return_value = _async_cm()

        await adapter._handle_message(discord_msg)

        assert len(captured) == 1
        inbound = captured[0]
        assert inbound.adapter_id == "discord"
        assert inbound.thread_id == "discord-42-99"
        assert inbound.content == "Hello bot!"
        assert inbound.reply_channel_id == "99"
        assert inbound.user_id == "42"

    async def test_handle_message_activates_typing_indicator(self) -> None:
        adapter = DiscordAdapter(token="fake-token")
        router, _ = _mock_router_with_dispatch()
        adapter._router = router  # type: ignore[assignment]

        typing_cm = _async_cm()
        discord_msg = MagicMock()
        discord_msg.author.id = 1
        discord_msg.author.bot = False
        discord_msg.channel.id = 2
        discord_msg.content = "hi"
        discord_msg.channel.typing.return_value = typing_cm

        await adapter._handle_message(discord_msg)

        typing_cm.__aenter__.assert_awaited_once()
        typing_cm.__aexit__.assert_awaited_once()

    async def test_handle_message_awaits_task_inside_typing(self) -> None:
        """Typing indicator must stay active until agent finishes."""
        adapter = DiscordAdapter(token="fake-token")
        order: list[str] = []

        async def fake_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            async def work() -> None:
                order.append("agent_done")

            return asyncio.create_task(work())

        router = MagicMock()
        router.dispatch = fake_dispatch
        adapter._router = router  # type: ignore[assignment]

        typing_cm = MagicMock()

        async def fake_aenter(*_: Any) -> None:
            order.append("typing_start")

        async def fake_aexit(*_: Any) -> bool:
            order.append("typing_end")
            return False

        typing_cm.__aenter__ = fake_aenter
        typing_cm.__aexit__ = fake_aexit

        discord_msg = MagicMock()
        discord_msg.author.id = 1
        discord_msg.author.bot = False
        discord_msg.channel.id = 2
        discord_msg.content = "go"
        discord_msg.channel.typing.return_value = typing_cm

        await adapter._handle_message(discord_msg)

        assert order == ["typing_start", "agent_done", "typing_end"]

    async def test_handle_message_no_router_logs_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = DiscordAdapter(token="fake-token")
        # _router is None by default

        discord_msg = MagicMock()
        discord_msg.author.id = 1
        discord_msg.author.bot = False
        discord_msg.channel.id = 2
        discord_msg.content = "hi"

        with caplog.at_level(logging.ERROR, logger="agent.adapters.discord_adapter"):
            await adapter._handle_message(discord_msg)  # must not raise

        assert len(caplog.records) > 0


class TestDiscordClientOnMessage:
    async def test_on_message_ignores_bot(self) -> None:
        adapter = DiscordAdapter(token="fake-token")
        adapter._handle_message = AsyncMock()

        bot_msg = MagicMock()
        bot_msg.author.bot = True
        bot_msg.content = "I am a bot"

        await adapter._client.on_message(bot_msg)

        adapter._handle_message.assert_not_called()

    async def test_on_message_calls_handle_for_humans(self) -> None:
        adapter = DiscordAdapter(token="fake-token")
        adapter._handle_message = AsyncMock()

        human_msg = MagicMock()
        human_msg.author.bot = False
        human_msg.content = "hello"

        await adapter._client.on_message(human_msg)

        adapter._handle_message.assert_awaited_once_with(human_msg)


# ===========================================================================
# HeartbeatAdapter
# ===========================================================================


class TestHeartbeatAdapterSend:
    async def test_send_response_logs_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = _outbound(
            content="All good.",
            adapter_id="heartbeat",
            reply_channel_id="log",
            msg_type="response",
            node_name="agent",
        )
        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        assert any("All good." in r.message for r in caplog.records)

    async def test_send_tool_call_logs_arrow(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = _outbound(content="bash(...)", msg_type="tool_call", node_name="agent")
        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        assert any("→" in r.message for r in caplog.records)

    async def test_send_tool_result_logs_arrow(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = _outbound(content="exit_code: 0", msg_type="tool_result", node_name="tools")
        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        assert any("←" in r.message for r in caplog.records)

    async def test_send_error_logs_at_error_level(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = _outbound(content="boom", msg_type="error")
        with caplog.at_level(logging.ERROR, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("boom" in r.message for r in error_records)

    async def test_send_unknown_type_logs_raw(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = OutboundMessage(
            adapter_id="heartbeat",
            reply_channel_id="log",
            content="weird",
            metadata={"msg_type": "alien"},
        )
        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        assert any("weird" in r.message for r in caplog.records)

    async def test_send_falls_back_to_agent_when_node_name_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter()
        msg = OutboundMessage(
            adapter_id="heartbeat",
            reply_channel_id="log",
            content="done",
            metadata={"msg_type": "response"},  # no node_name
        )
        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            await adapter.send(msg)
        assert any("[agent]" in r.message for r in caplog.records)


class TestHeartbeatAdapterStart:
    async def test_start_dispatches_correct_inbound(
        self, tmp_path: Path
    ) -> None:
        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("Check everything.", encoding="utf-8")

        adapter = HeartbeatAdapter(
            HeartbeatSettings(interval_seconds=1, prompt_file=str(prompt_file))
        )
        router, captured = _mock_router_with_dispatch()

        with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
            with pytest.raises(asyncio.CancelledError):
                await adapter.start(router)

        assert len(captured) == 1
        inbound = captured[0]
        assert inbound.content == "Check everything."
        assert inbound.thread_id == "heartbeat"
        assert inbound.reply_channel_id == "log"
        assert inbound.adapter_id == "heartbeat"
        assert inbound.user_id is None

    async def test_start_file_not_found_stops_gracefully(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = HeartbeatAdapter(
            HeartbeatSettings(prompt_file="nonexistent_file_xyz.md")
        )
        router, captured = _mock_router_with_dispatch()

        with caplog.at_level(logging.ERROR, logger="agent.adapters.heartbeat_adapter"):
            await adapter.start(router)  # must return, not raise

        assert len(captured) == 0
        assert any("nonexistent_file_xyz.md" in r.message for r in caplog.records)

    async def test_start_sleeps_with_configured_interval(
        self, tmp_path: Path
    ) -> None:
        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("ping", encoding="utf-8")

        adapter = HeartbeatAdapter(
            HeartbeatSettings(interval_seconds=42, prompt_file=str(prompt_file))
        )
        router, _ = _mock_router_with_dispatch()

        sleep_calls: list[float] = []

        async def record_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", new=record_sleep):
            with pytest.raises(asyncio.CancelledError):
                await adapter.start(router)

        assert sleep_calls == [42]

    async def test_start_runs_multiple_iterations(
        self, tmp_path: Path
    ) -> None:
        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("tick", encoding="utf-8")

        adapter = HeartbeatAdapter(
            HeartbeatSettings(interval_seconds=1, prompt_file=str(prompt_file))
        )
        router, captured = _mock_router_with_dispatch()

        call_count = 0

        async def sleep_twice(secs: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", new=sleep_twice):
            with pytest.raises(asyncio.CancelledError):
                await adapter.start(router)

        assert len(captured) == 2
        assert all(m.content == "tick" for m in captured)

    async def test_start_uses_default_settings_when_none_given(self) -> None:
        """Adapter without explicit settings falls back to HeartbeatSettings()."""
        adapter = HeartbeatAdapter()
        assert adapter._settings.interval_seconds == 600
        assert adapter._settings.prompt_file == "HEARTBEAT.md"
