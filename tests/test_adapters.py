"""Tests for Phase 2 — adapters and related config.

All tests run without a real LLM, Discord token, or interactive terminal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import nio
import pytest

from agent.adapters.batch_adapter import BatchAdapter
from agent.adapters.discord_adapter import DiscordAdapter, _DiscordClient
from agent.adapters.heartbeat_adapter import HeartbeatAdapter
from agent.adapters.terminal_adapter import TerminalAdapter
from agent.config import HeartbeatSettings, MatrixSettings, get_settings
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

if TYPE_CHECKING:
    from agent.adapters.matrix_adapter import MatrixAdapter


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

    def test_heartbeat_prompt_file_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("HEARTBEAT_PROMPT_FILE", "MY_BEAT.md")
        s = get_settings()
        assert s.heartbeat.prompt_file == "MY_BEAT.md"


# ===========================================================================
# MatrixSettings
# ===========================================================================


class TestMatrixSettings:
    def test_defaults(self) -> None:
        s = MatrixSettings()
        assert s.homeserver_url == ""
        assert s.access_token == ""
        assert s.user_id == ""
        assert s.device_id == ""
        assert s.store_path == ""
        assert s.ignore_unverified_devices is True

    def test_custom_values(self) -> None:
        s = MatrixSettings(
            homeserver_url="https://matrix.org",
            access_token="tok-abc",
            user_id="@bot:matrix.org",
            device_id="DEVICE123",
            store_path="./nio_store",
            ignore_unverified_devices=False,
        )
        assert s.homeserver_url == "https://matrix.org"
        assert s.access_token == "tok-abc"
        assert s.user_id == "@bot:matrix.org"
        assert s.device_id == "DEVICE123"
        assert s.store_path == "./nio_store"
        assert s.ignore_unverified_devices is False

    def test_settings_includes_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        s = get_settings()
        assert isinstance(s.matrix, MatrixSettings)

    def test_matrix_homeserver_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_HOMESERVER_URL", "https://example.com")
        s = get_settings()
        assert s.matrix.homeserver_url == "https://example.com"

    def test_matrix_access_token_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_abc")
        s = get_settings()
        assert s.matrix.access_token == "syt_abc"

    def test_matrix_user_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_USER_ID", "@bot:example.com")
        s = get_settings()
        assert s.matrix.user_id == "@bot:example.com"

    def test_matrix_device_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_DEVICE_ID", "DEV123")
        s = get_settings()
        assert s.matrix.device_id == "DEV123"

    def test_matrix_store_path_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_STORE_PATH", "./nio_store")
        s = get_settings()
        assert s.matrix.store_path == "./nio_store"

    def test_matrix_ignore_unverified_devices_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MATRIX_IGNORE_UNVERIFIED_DEVICES", "false")
        s = get_settings()
        assert s.matrix.ignore_unverified_devices is False

    def test_matrix_defaults_to_empty_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("MATRIX_HOMESERVER_URL", raising=False)
        monkeypatch.delenv("MATRIX_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("MATRIX_USER_ID", raising=False)
        monkeypatch.delenv("MATRIX_DEVICE_ID", raising=False)
        monkeypatch.delenv("MATRIX_STORE_PATH", raising=False)
        monkeypatch.delenv("MATRIX_IGNORE_UNVERIFIED_DEVICES", raising=False)
        s = get_settings()
        assert s.matrix.homeserver_url == ""
        assert s.matrix.access_token == ""
        assert s.matrix.user_id == ""
        assert s.matrix.device_id == ""
        assert s.matrix.store_path == ""
        assert s.matrix.ignore_unverified_devices is True


# TerminalAdapter
# ===========================================================================


class TestTerminalAdapterSend:
    async def test_send_response_prints_with_node_prefix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(
            _outbound(content="Hello!", msg_type="response", node_name="agent")
        )
        assert capsys.readouterr().out.strip() == "[agent] Hello!"

    async def test_send_tool_call_prints_arrow(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(
            _outbound(
                content="calculate({'expr': '1+1'})",
                msg_type="tool_call",
                node_name="agent",
            )
        )
        out = capsys.readouterr().out
        assert "→" in out
        assert "calculate" in out

    async def test_send_tool_result_prints_left_arrow(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        adapter = TerminalAdapter()
        await adapter.send(
            _outbound(content="2", msg_type="tool_result", node_name="tools")
        )
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


class TestBatchAdapter:
    async def test_start_dispatches_non_empty_lines_in_order(
        self, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "prompts.txt"
        input_file.write_text("alpha\n\n beta \n", encoding="utf-8")
        output_file = tmp_path / "out.jsonl"

        adapter = BatchAdapter(input_file=str(input_file), output_file=str(output_file))
        router = MagicMock()
        dispatched: list[InboundMessage] = []

        async def fake_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            dispatched.append(msg)

            async def _done() -> None:
                await adapter.send(
                    OutboundMessage(
                        adapter_id=msg.adapter_id,
                        reply_channel_id=msg.reply_channel_id,
                        content=msg.content.upper(),
                        metadata={
                            "msg_type": "response",
                            "node_name": "agent",
                            "line_number": msg.metadata.get("line_number"),
                            "thread_id": msg.thread_id,
                            "prompt": msg.content,
                        },
                    )
                )

            return asyncio.create_task(_done())

        router.dispatch = fake_dispatch

        await adapter.start(router)

        assert [item.content for item in dispatched] == ["alpha", "beta"]
        assert dispatched[0].thread_id.startswith("batch-")
        assert output_file.exists()

        lines = output_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert '"line_number": 1' in lines[0]
        assert '"prompt": "alpha"' in lines[0]
        assert '"line_number": 3' in lines[1]

    async def test_start_records_error_event_for_failed_dispatch(
        self, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "prompts.txt"
        input_file.write_text("broken\n", encoding="utf-8")
        output_file = tmp_path / "out.jsonl"

        router = MagicMock()

        async def fake_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            async def _done() -> None:
                raise RuntimeError("boom")

            return asyncio.create_task(_done())

        router.dispatch = fake_dispatch

        adapter = BatchAdapter(input_file=str(input_file), output_file=str(output_file))
        await adapter.start(router)

        lines = output_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = __import__("json").loads(lines[0])
        assert record["line_number"] == 1
        assert record["event_type"] == "error"
        assert "boom" in record["content"]


class TestTerminalAdapterProcessInput:
    """Tests for _process_input — the core dispatch + status logic."""

    async def test_dispatches_message(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()
        adapter._router = router

        await adapter._process_input("hello world")

        assert len(captured) == 1
        assert captured[0].content == "hello world"
        assert captured[0].adapter_id == "terminal"
        assert captured[0].thread_id == "terminal-cli"
        assert captured[0].reply_channel_id == "stdout"
        assert captured[0].user_id is None

    async def test_skips_empty_input(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()
        adapter._router = router

        await adapter._process_input("")
        await adapter._process_input("   ")

        assert len(captured) == 0

    async def test_ignores_quit_commands(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()
        adapter._router = router

        for cmd in ("quit", "Quit", "QUIT", "exit", "q"):
            captured.clear()
            await adapter._process_input(cmd)
            assert len(captured) == 0, f"'{cmd}' should not dispatch"

    async def test_sets_working_status_during_processing(self) -> None:
        adapter = TerminalAdapter()
        statuses: list[str] = []

        async def capture_status_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            statuses.append(adapter._status)

            async def noop() -> None:
                pass

            return asyncio.create_task(noop())

        router = MagicMock()
        router.dispatch = capture_status_dispatch
        adapter._router = router

        await adapter._process_input("hello")

        assert statuses == ["Working..."]

    async def test_resets_status_after_completion(self) -> None:
        adapter = TerminalAdapter()
        router, _ = _mock_router_with_dispatch()
        adapter._router = router

        await adapter._process_input("hello")

        assert adapter._status == "Idle"
        assert adapter._processing is False

    async def test_resets_status_on_error(self) -> None:
        adapter = TerminalAdapter()

        async def failing_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
            async def fail() -> None:
                raise RuntimeError("boom")

            return asyncio.create_task(fail())

        router = MagicMock()
        router.dispatch = failing_dispatch
        adapter._router = router

        await adapter._process_input("hello")  # must not raise

        assert adapter._status == "Idle"
        assert adapter._processing is False

    async def test_awaits_dispatch_task(self) -> None:
        """The task must be fully awaited before _process_input returns."""
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
        adapter._router = router

        await adapter._process_input("first")

        assert order == ["task_start", "task_end"]
        assert adapter._processing is False

    async def test_ignores_input_while_processing(self) -> None:
        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()
        adapter._router = router
        adapter._processing = True

        await adapter._process_input("ignored")

        assert len(captured) == 0


class TestTerminalAdapterAcceptHandler:
    """Tests for the Buffer accept handler (fires on Enter)."""

    @staticmethod
    def _make_adapter_with_mock_app() -> TerminalAdapter:
        adapter = TerminalAdapter()
        adapter._app = MagicMock()
        return adapter

    async def test_quit_exits_app(self) -> None:
        adapter = self._make_adapter_with_mock_app()
        buff = MagicMock()
        buff.text = "quit"

        handler = adapter._make_accept_handler()
        handler(buff)

        adapter._app.exit.assert_called_once()
        buff.reset.assert_called_once()

    async def test_quit_variants_exit_app(self) -> None:
        for cmd in ("quit", "Quit", "QUIT", "exit", "q"):
            adapter = self._make_adapter_with_mock_app()
            buff = MagicMock()
            buff.text = cmd

            handler = adapter._make_accept_handler()
            handler(buff)

            adapter._app.exit.assert_called_once()

    async def test_empty_input_does_not_exit_or_dispatch(self) -> None:
        adapter = self._make_adapter_with_mock_app()
        buff = MagicMock()
        buff.text = "   "

        handler = adapter._make_accept_handler()
        handler(buff)

        adapter._app.exit.assert_not_called()
        adapter._app.create_background_task.assert_not_called()
        buff.reset.assert_called_once()

    async def test_input_while_processing_ignored(self) -> None:
        adapter = self._make_adapter_with_mock_app()
        adapter._processing = True
        buff = MagicMock()
        buff.text = "hello"

        handler = adapter._make_accept_handler()
        handler(buff)

        adapter._app.create_background_task.assert_not_called()
        buff.reset.assert_called_once()

    async def test_valid_input_creates_background_task(self) -> None:
        adapter = self._make_adapter_with_mock_app()
        created: list[Any] = []

        def fake_create_task(coro: Any) -> None:
            created.append(coro)
            coro.close()

        adapter._app.create_background_task = fake_create_task
        buff = MagicMock()
        buff.text = "hello world"

        handler = adapter._make_accept_handler()
        handler(buff)

        assert len(created) == 1
        buff.reset.assert_called_once()


class TestTerminalAdapterStart:
    async def test_start_skips_non_interactive_stdio(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = TerminalAdapter()
        router, captured = _mock_router_with_dispatch()

        with patch.object(adapter, "_interactive_stdio_available", return_value=False):
            with caplog.at_level(
                logging.WARNING, logger="agent.adapters.terminal_adapter"
            ):
                await adapter.start(router)

        assert len(captured) == 0
        assert any(
            "interactive stdin/stdout" in record.message for record in caplog.records
        )


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
        await adapter.send(
            _outbound(msg_type="tool_call", adapter_id="discord", reply_channel_id="99")
        )
        ch.send.assert_not_awaited()

    async def test_send_skips_tool_result(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        await adapter.send(
            _outbound(
                msg_type="tool_result", adapter_id="discord", reply_channel_id="99"
            )
        )
        ch.send.assert_not_awaited()

    async def test_send_skips_empty_content(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        msg = _outbound(
            content="", msg_type="response", adapter_id="discord", reply_channel_id="99"
        )
        await adapter.send(msg)
        ch.send.assert_not_awaited()

    async def test_send_chunks_long_message(self) -> None:
        adapter, ch = self._adapter_with_mock_channel()
        # 4001 chars → chunks of 2000 + 2000 + 1 = 3 sends
        big = "x" * 4001
        msg = _outbound(
            content=big,
            msg_type="response",
            adapter_id="discord",
            reply_channel_id="99",
        )
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

        msg = _outbound(
            content="hi",
            msg_type="response",
            adapter_id="discord",
            reply_channel_id="99",
        )
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

        msg = _outbound(
            content="hi",
            msg_type="response",
            adapter_id="discord",
            reply_channel_id="99",
        )
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
        msg = _outbound(
            content="exit_code: 0", msg_type="tool_result", node_name="tools"
        )
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
    async def test_start_dispatches_correct_inbound(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("Check everything.", encoding="utf-8")

        adapter = HeartbeatAdapter(
            HeartbeatSettings(interval_seconds=1, prompt_file=str(prompt_file))
        )
        router, captured = _mock_router_with_dispatch()

        with patch(
            "asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())
        ):
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

    async def test_start_sleeps_with_configured_interval(self, tmp_path: Path) -> None:
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

    async def test_start_runs_multiple_iterations(self, tmp_path: Path) -> None:
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

    async def test_start_stores_router_reference(self, tmp_path: Path) -> None:
        """Router passed to start() must be reachable from send() for forwarding."""
        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("ping", encoding="utf-8")

        adapter = HeartbeatAdapter(HeartbeatSettings(prompt_file=str(prompt_file)))
        assert adapter._router is None

        router, _ = _mock_router_with_dispatch()
        with patch(
            "asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())
        ):
            with pytest.raises(asyncio.CancelledError):
                await adapter.start(router)

        assert adapter._router is router


# ===========================================================================
# HeartbeatAdapter — forwarding
# ===========================================================================


def _mock_router_with_forwarding() -> tuple[
    MagicMock, list[InboundMessage], list[OutboundMessage]
]:
    """Router mock that records both dispatched inbound and send_to outbound messages."""
    dispatched: list[InboundMessage] = []
    forwarded: list[OutboundMessage] = []

    async def _noop() -> None:
        pass

    async def fake_dispatch(msg: InboundMessage) -> asyncio.Task[None]:
        dispatched.append(msg)
        return asyncio.create_task(_noop())

    async def fake_send_to(msg: OutboundMessage) -> None:
        forwarded.append(msg)

    router = MagicMock()
    router.dispatch = fake_dispatch
    router.send_to = fake_send_to
    return router, dispatched, forwarded


class TestHeartbeatAdapterForwarding:
    """Forwarding no longer happens inside send().

    The agent queues notifications via the ``send_notification`` tool,
    and the adapter dispatches them after each agent run via
    ``consume_notifications()``.
    """

    async def test_send_does_not_forward(self) -> None:
        """send() only logs; forwarding is handled separately."""
        settings = HeartbeatSettings(
            output_adapter_id="discord",
            output_channel_id="99999",
        )
        adapter = HeartbeatAdapter(settings)
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        await adapter.send(
            _outbound(
                content="Heartbeat done.",
                msg_type="response",
                adapter_id="heartbeat",
                reply_channel_id="log",
            )
        )

        assert len(forwarded) == 0

    async def test_notifications_forwarded_after_run(self) -> None:
        """Notifications queued via send_notification are forwarded post-run."""
        from agent.tools.tools_notifications import consume_notifications

        settings = HeartbeatSettings(
            output_adapter_id="discord",
            output_channel_id="99999",
        )
        adapter = HeartbeatAdapter(settings)
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        # Simulate what happens during an agent run:
        # agent calls send_notification tool
        from agent.tools.tools_notifications import send_notification

        await send_notification.ainvoke({"content": "Weather changed to cloudy"})

        # After the run, the adapter consumes notifications
        assert (await adapter._maybe_forward_notifications()) is True

        assert len(forwarded) == 1
        assert forwarded[0].adapter_id == "discord"
        assert forwarded[0].reply_channel_id == "99999"
        assert forwarded[0].content == "Weather changed to cloudy"

    async def test_no_forwarding_when_no_notifications(self) -> None:
        """If no send_notification call, nothing is forwarded."""
        from agent.tools.tools_notifications import consume_notifications

        settings = HeartbeatSettings(
            output_adapter_id="discord",
            output_channel_id="99999",
        )
        adapter = HeartbeatAdapter(settings)
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        assert (await adapter._maybe_forward_notifications()) is False
        assert len(forwarded) == 0

    async def test_no_forwarding_without_output_adapter(self) -> None:
        adapter = HeartbeatAdapter(HeartbeatSettings())
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        from agent.tools.tools_notifications import send_notification

        await send_notification.ainvoke({"content": "test"})
        assert (await adapter._maybe_forward_notifications()) is False
        assert len(forwarded) == 0

    async def test_no_forwarding_when_only_adapter_set(self) -> None:
        settings = HeartbeatSettings(output_adapter_id="discord", output_channel_id="")
        adapter = HeartbeatAdapter(settings)
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        from agent.tools.tools_notifications import send_notification

        await send_notification.ainvoke({"content": "test"})
        assert (await adapter._maybe_forward_notifications()) is False
        assert len(forwarded) == 0

    async def test_no_forwarding_when_only_channel_set(self) -> None:
        settings = HeartbeatSettings(output_adapter_id="", output_channel_id="99999")
        adapter = HeartbeatAdapter(settings)
        router, _, forwarded = _mock_router_with_forwarding()
        adapter._router = router  # type: ignore[assignment]

        from agent.tools.tools_notifications import send_notification

        await send_notification.ainvoke({"content": "test"})
        assert (await adapter._maybe_forward_notifications()) is False
        assert len(forwarded) == 0

    async def test_start_forwarding_config_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        prompt_file = tmp_path / "beat.md"
        prompt_file.write_text("ping", encoding="utf-8")
        settings = HeartbeatSettings(
            prompt_file=str(prompt_file),
            output_adapter_id="discord",
            output_channel_id="12345",
        )
        adapter = HeartbeatAdapter(settings)
        router, _ = _mock_router_with_dispatch()

        with caplog.at_level(logging.INFO, logger="agent.adapters.heartbeat_adapter"):
            with patch(
                "asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())
            ):
                with pytest.raises(asyncio.CancelledError):
                    await adapter.start(router)

        assert any("discord:12345" in r.message for r in caplog.records)


class TestHeartbeatSettingsForwarding:
    def test_defaults_have_no_forwarding(self) -> None:
        s = HeartbeatSettings()
        assert s.output_adapter_id == ""
        assert s.output_channel_id == ""

    def test_output_adapter_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("HEARTBEAT_OUTPUT_ADAPTER", "discord")
        monkeypatch.setenv("HEARTBEAT_OUTPUT_CHANNEL", "123456789")
        from agent.config import get_settings

        s = get_settings()
        assert s.heartbeat.output_adapter_id == "discord"
        assert s.heartbeat.output_channel_id == "123456789"


# ===========================================================================
# MatrixAdapter
# ===========================================================================


def _matrix_outbound(
    content: str = "hello",
    msg_type: str = "response",
    reply_channel_id: str = "!room:matrix.org",
    event_id: str | None = "evt-001",
) -> OutboundMessage:
    meta: dict[str, str] = {"msg_type": msg_type}
    if event_id is not None:
        meta["event_id"] = event_id
    return OutboundMessage(
        adapter_id="matrix",
        reply_channel_id=reply_channel_id,
        content=content,
        metadata=meta,
    )


def _make_matrix_adapter() -> "MatrixAdapter":
    from agent.adapters.matrix_adapter import MatrixAdapter
    from agent.config import MatrixSettings

    settings = MatrixSettings(
        homeserver_url="https://matrix.example.com",
        access_token="syt_fake",
        user_id="@bot:example.com",
    )
    adapter = MatrixAdapter(settings)
    # Replace the nio client with a mock so no real network calls are made.
    adapter._client = AsyncMock()
    return adapter


class TestMatrixAdapterSend:
    async def test_send_response_calls_room_send(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock(return_value=MagicMock(spec=[]))
        await adapter.send(
            _matrix_outbound(content="Hello Matrix!", msg_type="response")
        )
        adapter._client.room_send.assert_awaited_once()
        call_kwargs = adapter._client.room_send.call_args
        assert call_kwargs.kwargs["room_id"] == "!room:matrix.org"
        assert call_kwargs.kwargs["content"]["body"] == "Hello Matrix!"
        assert call_kwargs.kwargs["content"]["msgtype"] == "m.text"
        assert call_kwargs.kwargs["ignore_unverified_devices"] is True

    async def test_send_error_calls_room_send(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock(return_value=MagicMock(spec=[]))
        await adapter.send(
            _matrix_outbound(content="Something went wrong", msg_type="error")
        )
        adapter._client.room_send.assert_awaited_once()

    async def test_send_tool_call_is_dropped(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock()
        await adapter.send(_matrix_outbound(msg_type="tool_call"))
        adapter._client.room_send.assert_not_awaited()

    async def test_send_tool_result_is_dropped(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock()
        await adapter.send(_matrix_outbound(msg_type="tool_result"))
        adapter._client.room_send.assert_not_awaited()

    async def test_send_empty_content_is_dropped(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock()
        await adapter.send(_matrix_outbound(content="", msg_type="response"))
        adapter._client.room_send.assert_not_awaited()

    async def test_send_includes_in_reply_to_when_event_id_present(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock(return_value=MagicMock(spec=[]))
        await adapter.send(_matrix_outbound(event_id="$evt123"))
        content = adapter._client.room_send.call_args.kwargs["content"]
        assert content["m.relates_to"] == {"m.in_reply_to": {"event_id": "$evt123"}}

    async def test_send_omits_in_reply_to_when_no_event_id(self) -> None:
        adapter = _make_matrix_adapter()
        adapter._client.room_send = AsyncMock(return_value=MagicMock(spec=[]))
        await adapter.send(_matrix_outbound(event_id=None))
        content = adapter._client.room_send.call_args.kwargs["content"]
        assert "m.relates_to" not in content

    async def test_send_logs_error_on_room_send_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = _make_matrix_adapter()
        error_response = MagicMock(spec=nio.RoomSendError)
        adapter._client.room_send = AsyncMock(return_value=error_response)

        with caplog.at_level(logging.ERROR, logger="agent.adapters.matrix_adapter"):
            await adapter.send(_matrix_outbound())  # must not raise

        assert any("room_send failed" in r.message for r in caplog.records)


class TestMatrixAdapterStart:
    def _make_nio_event(
        self,
        sender: str = "@user:matrix.org",
        body: str = "Hello!",
        event_id: str = "$evt001",
    ) -> nio.RoomMessageText:

        source = {
            "event_id": event_id,
            "sender": sender,
            "origin_server_ts": 1000,
            "type": "m.room.message",
            "content": {"msgtype": "m.text", "body": body},
            "room_id": "!room:matrix.org",
        }
        return nio.RoomMessageText.from_dict(source)

    def _make_nio_room(
        self,
        room_id: str = "!room:matrix.org",
        name: str = "Test Room",
    ) -> nio.MatrixRoom:

        room = nio.MatrixRoom(room_id=room_id, own_user_id="@bot:example.com")
        room.name = name
        return room

    async def test_own_messages_are_not_dispatched(self) -> None:
        adapter = _make_matrix_adapter()
        router, captured = _mock_router_with_dispatch()

        # Trigger the callback manually with bot's own sender.
        event = self._make_nio_event(sender="@bot:example.com")
        room = self._make_nio_room()

        # Extract callback by patching add_event_callback to capture it.
        captured_cb: list[Any] = []
        adapter._client.add_event_callback = MagicMock(
            side_effect=lambda cb, _: captured_cb.append(cb)
        )

        first_response = MagicMock(spec=nio.SyncResponse)
        first_response.next_batch = "tok_001"
        adapter._client.sync = AsyncMock(return_value=first_response)
        adapter._client.sync_forever = AsyncMock(side_effect=asyncio.CancelledError())
        adapter._client.stop_sync_forever = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await adapter.start(router)

        assert len(captured_cb) == 2
        await captured_cb[0](room, event)
        assert len(captured) == 0
        adapter._client.stop_sync_forever.assert_called_once_with()

    async def test_valid_message_builds_correct_inbound(self) -> None:
        adapter = _make_matrix_adapter()
        router, captured = _mock_router_with_dispatch()

        event = self._make_nio_event(
            sender="@alice:matrix.org",
            body="What is the answer?",
            event_id="$evt-abc",
        )
        room = self._make_nio_room(
            room_id="!general:matrix.org",
            name="General",
        )

        captured_cb: list[Any] = []
        adapter._client.add_event_callback = MagicMock(
            side_effect=lambda cb, _: captured_cb.append(cb)
        )

        first_response = MagicMock(spec=nio.SyncResponse)
        first_response.next_batch = "tok_001"
        adapter._client.sync = AsyncMock(return_value=first_response)
        adapter._client.sync_forever = AsyncMock(side_effect=asyncio.CancelledError())
        adapter._client.stop_sync_forever = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await adapter.start(router)

        await captured_cb[0](room, event)

        assert len(captured) == 1
        inbound = captured[0]
        assert inbound.adapter_id == "matrix"
        assert inbound.thread_id == "matrix-!general:matrix.org-@alice:matrix.org"
        assert inbound.content == "What is the answer?"
        assert inbound.reply_channel_id == "!general:matrix.org"
        assert inbound.user_id == "@alice:matrix.org"
        assert inbound.metadata["event_id"] == "$evt-abc"
        assert inbound.metadata["room_name"] == "General"

    async def test_sync_error_triggers_backoff(self) -> None:
        adapter = _make_matrix_adapter()
        router, _ = _mock_router_with_dispatch()

        adapter._client.add_event_callback = MagicMock()
        call_count = 0

        async def fail_twice(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("timeout")
            response = MagicMock(spec=nio.SyncResponse)
            response.next_batch = "tok_001"
            return response

        adapter._client.sync = fail_twice
        adapter._client.sync_forever = AsyncMock(side_effect=asyncio.CancelledError())
        adapter._client.stop_sync_forever = MagicMock()
        adapter._client.next_batch = None
        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("agent.adapters.matrix_adapter.asyncio.sleep", new=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await adapter.start(router)

        assert len(sleep_calls) == 2
        # Second backoff must be >= first (exponential).
        assert sleep_calls[1] >= sleep_calls[0]

    async def test_next_batch_token_passed_on_subsequent_sync(self) -> None:
        """Bootstrap sync token must be passed into sync_forever."""

        adapter = _make_matrix_adapter()
        router, _ = _mock_router_with_dispatch()
        adapter._client.add_event_callback = MagicMock()
        adapter._client.stop_sync_forever = MagicMock()

        first_response = MagicMock(spec=nio.SyncResponse)
        first_response.next_batch = "tok_001"
        adapter._client.sync = AsyncMock(return_value=first_response)
        adapter._client.sync_forever = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await adapter.start(router)

        adapter._client.sync.assert_awaited_once_with(
            timeout=0,
            full_state=True,
            since=None,
        )
        adapter._client.sync_forever.assert_awaited_once_with(
            timeout=30_000,
            since="tok_001",
        )

    async def test_decryption_failures_are_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = _make_matrix_adapter()
        router, _ = _mock_router_with_dispatch()
        room = self._make_nio_room(room_id="!secure:matrix.org", name="Secure")
        event = nio.MegolmEvent.from_dict(
            {
                "event_id": "$enc001",
                "sender": "@alice:matrix.org",
                "origin_server_ts": 1000,
                "type": "m.room.encrypted",
                "content": {
                    "algorithm": "m.megolm.v1.aes-sha2",
                    "ciphertext": "abc",
                    "device_id": "DEV1",
                    "sender_key": "key",
                    "session_id": "sess",
                },
                "room_id": "!secure:matrix.org",
            }
        )

        captured_cb: list[Any] = []
        adapter._client.add_event_callback = MagicMock(
            side_effect=lambda cb, _: captured_cb.append(cb)
        )
        first_response = MagicMock(spec=nio.SyncResponse)
        first_response.next_batch = "tok_001"
        adapter._client.sync = AsyncMock(return_value=first_response)
        adapter._client.sync_forever = AsyncMock(side_effect=asyncio.CancelledError())
        adapter._client.stop_sync_forever = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await adapter.start(router)

        with caplog.at_level(logging.WARNING, logger="agent.adapters.matrix_adapter"):
            await captured_cb[1](room, event)

        assert any("could not decrypt message" in r.message for r in caplog.records)

    async def test_init_loads_store_when_store_path_configured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from agent.adapters.matrix_adapter import MatrixAdapter
        from agent.config import MatrixSettings

        load_store = MagicMock()

        class DummyClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.user_id = ""
                self.device_id = ""
                self.access_token = ""
                self.load_store = load_store

        monkeypatch.setattr(
            "agent.adapters.matrix_adapter.nio.AsyncClient", DummyClient
        )

        settings = MatrixSettings(
            homeserver_url="https://matrix.example.com",
            access_token="syt_fake",
            user_id="@bot:example.com",
            device_id="DEVICE123",
            store_path=str(tmp_path / "nio_store"),
        )

        adapter = MatrixAdapter(settings)

        assert Path(settings.store_path).is_dir()
        load_store.assert_called_once_with()
        assert adapter._client.user_id == "@bot:example.com"
        assert adapter._client.device_id == "DEVICE123"
        assert adapter._client.access_token == "syt_fake"
