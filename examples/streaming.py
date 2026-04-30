"""Streaming example — shows token-by-token output with event metadata.

Run with:
    uv run python examples/streaming.py

This script demonstrates:
- ``graph.stream()`` with ``stream_mode="updates"`` to see each node's output.
- ``graph.astream_events()`` for fine-grained streaming (LLM tokens, tool calls).

Set your API keys in ``.env`` before running.
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from agent.graph import graph


# ---------------------------------------------------------------------------
# 1. Synchronous streaming — node-level updates
# ---------------------------------------------------------------------------


def run_sync(question: str) -> None:
    """Stream node-level updates synchronously."""
    print("=" * 60)
    print(f"Question: {question}")
    print("=" * 60)

    for step in graph.stream(
        {"messages": [HumanMessage(content=question)]},
        stream_mode="updates",
    ):
        node_name, node_output = next(iter(step.items()))
        last_msg = node_output["messages"][-1]

        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            calls = ", ".join(tc["name"] for tc in last_msg.tool_calls)
            print(f"[{node_name}] → tool calls: {calls}")
        elif hasattr(last_msg, "name"):  # ToolMessage
            print(f"[{node_name}] ← tool result: {last_msg.content[:80]}")
        else:
            print(f"[{node_name}] {last_msg.content}")

    print()


# ---------------------------------------------------------------------------
# 2. Async streaming — LLM token-level events
# ---------------------------------------------------------------------------


async def run_async(question: str) -> None:
    """Stream individual LLM tokens using ``astream_events``."""
    print("=" * 60)
    print(f"Question (async token stream): {question}")
    print("=" * 60)
    print("Agent: ", end="", flush=True)

    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=question)]},
        version="v2",
    ):
        kind = event["event"]
        # Print each LLM output token as it arrives.
        if kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if chunk.content:
                print(chunk.content, end="", flush=True)

    print("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # Sync demo
    run_sync("What is (123 * 456) + 789? Show your work.")

    # Async demo
    asyncio.run(run_async("What time is it right now (UTC)?"))
