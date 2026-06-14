"""Batch adapter for one-shot line-oriented prompt execution."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

logger = logging.getLogger(__name__)


class BatchAdapter(BaseAdapter):
    """Read prompts from a file, dispatch them sequentially, and write JSONL output."""

    adapter_id = "batch"

    def __init__(self, input_file: str, output_file: str) -> None:
        self._input_file = Path(input_file)
        self._output_file = Path(output_file)
        self._output_file.parent.mkdir(parents=True, exist_ok=True)

    async def start(self, router: MessageRouter) -> None:
        """Process each non-empty line in order and write structured event records."""
        try:
            lines = self._input_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            logger.error("BatchAdapter input file not found: %s", self._input_file)
            return

        with self._output_file.open("w", encoding="utf-8") as handle:
            for index, raw_line in enumerate(lines, start=1):
                prompt = raw_line.strip()
                if not prompt:
                    continue

                inbound = InboundMessage(
                    adapter_id=self.adapter_id,
                    thread_id=f"batch-{index}",
                    content=prompt,
                    reply_channel_id="jsonl",
                    user_id=None,
                    metadata={"line_number": index, "prompt": prompt},
                )
                try:
                    task = await router.dispatch(inbound)
                    await task
                except Exception as exc:  # noqa: BLE001
                    record = {
                        "line_number": index,
                        "thread_id": inbound.thread_id,
                        "prompt": prompt,
                        "event_type": "error",
                        "content": f"{exc!s:.200}",
                    }
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()

    async def send(self, message: OutboundMessage) -> None:
        """Write each structured outbound event as a JSONL record."""
        record = {
            "line_number": message.metadata.get("line_number"),
            "thread_id": message.metadata.get("thread_id"),
            "prompt": message.metadata.get("prompt"),
            "event_type": message.msg_type,
            "content": message.content,
            "node_name": message.metadata.get("node_name"),
        }
        with self._output_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
