"""Terminal adapter — interactive full-screen REPL with a live status toolbar.

The adapter uses a ``prompt_toolkit`` :class:`~prompt_toolkit.Application`
whose layout is split into a scrollable output area, a single-line input,
and a reverse-video toolbar.  A background refresher calls
``app.invalidate()`` at a fixed interval so the toolbar reflects the
current processing state in real time — even while the agent is busy and
the user is not typing.  This replaces the previous ``prompt_async`` loop,
which could only refresh the toolbar while the prompt was active.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

logger = logging.getLogger(__name__)

_QUIT_COMMANDS = {"quit", "exit", "q"}
_REFRESH_INTERVAL = 0.3
_SPINNER_FRAMES = "|/-\\"


class TerminalAdapter(BaseAdapter):
    """Interactive full-screen terminal adapter with a live status toolbar.

    Thread ID
    ---------
    All messages share the single thread ``"terminal-cli"`` so the LangGraph
    checkpointer maintains a single conversation history for the session.

    Shutdown
    --------
    - ``quit`` / ``exit`` / ``q`` → exits the application.
    - ``Ctrl-D`` (EOF) → same.
    - ``Ctrl-C`` → same.
    """

    adapter_id = "terminal"

    def __init__(self) -> None:
        self._output_buffer: Buffer | None = None
        self._input_buffer: Buffer | None = None
        self._app: Application[Any] | None = None
        self._status = "Idle"
        self._processing = False
        self._spinner_frame = 0
        self._router: MessageRouter | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _interactive_stdio_available(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def _get_toolbar(self) -> str:
        if self._processing:
            frame = self._spinner_frame % len(_SPINNER_FRAMES)
            return f" {_SPINNER_FRAMES[frame]} Working... "
        return " Idle "

    def _append_output(self, text: str) -> None:
        """Append *text* to the output buffer, or fall back to ``print``.

        When the full-screen application is not running (e.g. unit tests,
        :class:`~agent.adapters.prompt_adapter.PromptAdapter`) the text is
        written to stdout via :func:`print` so existing behaviour is
        preserved.
        """
        if self._output_buffer is not None:
            new_text = self._output_buffer.text + text + "\n"
            self._output_buffer.set_document(
                Document(new_text, cursor_position=len(new_text)),
                bypass_readonly=True,
            )
            if self._app is not None:
                self._app.invalidate()
        else:
            print(text)

    # ------------------------------------------------------------------
    # BaseAdapter API
    # ------------------------------------------------------------------

    async def send(self, message: OutboundMessage) -> None:
        """Deliver *message* to the output area with type-appropriate formatting."""
        node = message.metadata.get("node_name") or "agent"

        if message.msg_type == "tool_call":
            line = f"[{node}] \u2192 {message.content}"
        elif message.msg_type == "tool_result":
            line = f"[{node}] \u2190 {message.content}"
        elif message.msg_type == "response":
            line = f"[{node}] {message.content}"
        elif message.msg_type == "error":
            line = f"Error: {message.content}"
        else:
            line = message.content

        self._append_output(line)

    async def _process_input(self, user_input: str) -> None:
        """Dispatch *user_input* to the router, updating the toolbar status.

        Sets the toolbar to "Working…" for the duration of the dispatched
        task and resets it to "Idle" on completion — even if the task
        raises.  Input is ignored while another task is already running.
        """
        text = user_input.strip()
        if not text or self._processing:
            return
        if text.lower() in _QUIT_COMMANDS:
            return

        assert self._router is not None

        inbound = InboundMessage(
            adapter_id=self.adapter_id,
            thread_id="terminal-cli",
            content=text,
            reply_channel_id="stdout",
            user_id=None,
        )
        self._processing = True
        self._status = "Working..."
        try:
            task = await self._router.dispatch(inbound)
            await task
        except Exception:
            logger.exception("Error processing terminal input")
        finally:
            self._processing = False
            self._status = "Idle"

    def _make_accept_handler(self) -> Callable[[Buffer], bool]:
        """Return the ``Buffer.accept_handler`` fired when the user presses Enter.

        Input is ignored while the agent is processing (``_processing`` flag
        is set) so messages on the same thread are handled sequentially.

        Returns ``False`` because the buffer is reset manually inside the
        handler (prompt_toolkit would otherwise reset it again).
        """

        def accept(buff: Buffer) -> bool:
            text = buff.text.strip()
            buff.reset()
            if not text or self._processing:
                return False
            if text.lower() in _QUIT_COMMANDS:
                if self._app is not None:
                    self._app.exit()
                return False
            self._append_output(f"You: {text}")
            if self._app is not None:
                self._app.create_background_task(self._process_input(text))
            return False

        return accept

    async def start(self, router: MessageRouter) -> None:
        """Run the full-screen REPL until the user quits or EOF is reached."""
        if not self._interactive_stdio_available():
            logger.warning(
                "TerminalAdapter requires interactive stdin/stdout; "
                "skipping terminal REPL."
            )
            return

        self._router = router
        self._output_buffer = Buffer()
        self._input_buffer = Buffer(multiline=False)
        self._input_buffer.accept_handler = self._make_accept_handler()

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-d")
        def _exit(event: KeyPressEvent) -> None:
            event.app.exit()

        output_window = Window(
            content=BufferControl(buffer=self._output_buffer),
            wrap_lines=True,
        )
        separator = Window(height=1, char="-")
        input_window = Window(
            content=BufferControl(buffer=self._input_buffer),
            height=1,
        )
        toolbar_window = Window(
            content=FormattedTextControl(self._get_toolbar),
            height=1,
            style="reverse",
        )

        root_container = HSplit(
            [output_window, separator, input_window, toolbar_window]
        )
        layout = Layout(root_container, focused_element=self._input_buffer)

        self._app = Application(layout=layout, key_bindings=kb, full_screen=True)

        self._append_output("Agent ready  (type 'quit' or Ctrl-D to exit)")

        async def refresher() -> None:
            while True:
                await asyncio.sleep(_REFRESH_INTERVAL)
                if self._processing:
                    self._spinner_frame += 1
                if self._app is not None:
                    self._app.invalidate()

        refresh_task = asyncio.create_task(refresher())
        try:
            await self._app.run_async()
        finally:
            refresh_task.cancel()
            self._app = None

        print("Goodbye.")
