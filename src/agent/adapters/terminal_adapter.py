"""Terminal adapter — interactive REPL over stdin/stdout."""

from __future__ import annotations

import logging
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

logger = logging.getLogger(__name__)

_QUIT_COMMANDS = {"quit", "exit", "q"}


class TerminalAdapter(BaseAdapter):
    """Interactive terminal adapter.

    Presents a ``prompt_toolkit`` REPL.  Each user input is dispatched to the
    router and the adapter **awaits** the resulting task so that the agent's
    full response is printed before the next prompt appears.

    Thread ID
    ---------
    All messages share the single thread ``"terminal-cli"`` so the LangGraph
    checkpointer maintains a single conversation history for the session.

    Shutdown
    --------
    - ``quit`` / ``exit`` / ``q`` → prints "Goodbye." and returns from
      :meth:`start`, ending the coroutine normally.
    - ``Ctrl-D`` (EOF) → same; prints "Goodbye.".
    - ``Ctrl-C`` (KeyboardInterrupt) → re-raised so ``asyncio.run()`` can
      cancel all tasks and exit cleanly.
    """

    adapter_id = "terminal"

    def __init__(self) -> None:
        self._session: PromptSession[str] | None = None

    def _interactive_stdio_available(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def _get_session(self) -> PromptSession[str]:
        if self._session is None:
            self._session = PromptSession(history=InMemoryHistory())
        return self._session

    async def start(self, router: MessageRouter) -> None:
        """Run the REPL until the user quits or EOF is reached."""
        if self._session is None and not self._interactive_stdio_available():
            logger.warning(
                "TerminalAdapter requires interactive stdin/stdout; skipping terminal REPL."
            )
            return

        session = self._get_session()
        print("Agent ready  (type 'quit' or Ctrl-D to exit)\n")

        while True:
            try:
                user_input = (await session.prompt_async("You: ")).strip()
            except EOFError:
                print("\nGoodbye.")
                return
            except KeyboardInterrupt:
                # Re-raise so the event loop / asyncio.run() handles shutdown.
                raise

            if not user_input:
                continue
            if user_input.lower() in _QUIT_COMMANDS:
                print("Goodbye.")
                return

            inbound = InboundMessage(
                adapter_id=self.adapter_id,
                thread_id="terminal-cli",
                content=user_input,
                reply_channel_id="stdout",
                user_id=None,
            )
            # Await the task so the full response is printed before the next prompt.
            task = await router.dispatch(inbound)
            await task

    async def send(self, message: OutboundMessage) -> None:
        """Print *message* to stdout with formatting appropriate to its type."""
        node = message.metadata.get("node_name") or "agent"

        if message.msg_type == "tool_call":
            print(f"[{node}] \u2192 {message.content}")
        elif message.msg_type == "tool_result":
            print(f"[{node}] \u2190 {message.content}")
        elif message.msg_type == "response":
            print(f"[{node}] {message.content}")
        elif message.msg_type == "error":
            print(f"Error: {message.content}")
        else:
            # Unknown / future message types — print raw.
            print(message.content)
