"""One-shot prompt adapter for direct CLI execution."""

from __future__ import annotations

from agent.adapters.terminal_adapter import TerminalAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter


class PromptAdapter(TerminalAdapter):
    """Dispatch a single prompt through the router and print the streamed output."""

    adapter_id = "prompt"

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    async def start(self, router: MessageRouter) -> None:
        """Dispatch the configured prompt exactly once and wait for completion."""
        inbound = InboundMessage(
            adapter_id=self.adapter_id,
            thread_id="prompt-cli",
            content=self._prompt,
            reply_channel_id="stdout",
            user_id=None,
        )
        task = await router.dispatch(inbound)
        await task

    async def send(self, message: OutboundMessage) -> None:
        """Reuse terminal formatting for one-shot prompt output."""
        await super().send(message)
