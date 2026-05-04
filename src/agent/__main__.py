"""CLI entry point — ``uv run agent`` or ``python -m agent``."""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage

from agent.graph import graph

# Each session gets its own thread so the checkpointer maintains history.
_CONFIG = {"configurable": {"thread_id": "cli-session"}}


def main() -> None:
    """Run the agent in an interactive REPL loop."""
    print("LangGraph ReAct Agent  (type 'quit' or Ctrl-C to exit)\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Goodbye.")
            sys.exit(0)

        for step in graph.stream(
            {"messages": [HumanMessage(content=user_input)]},
            stream_mode="updates",
            config=_CONFIG,
        ):
            node_name, node_output = next(iter(step.items()))
            last_msg = node_output["messages"][-1]

            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                calls = ", ".join(tc["name"]+"("+str(tc["args"])+")" for tc in last_msg.tool_calls)
                print(f"[{node_name}] → tool calls: {calls}")
            elif hasattr(last_msg, "name") and last_msg.name != None:  # ToolMessage
                print(f"[{node_name}] ← tool result: {last_msg.content[:180]}")
            else:
                print(f"[{node_name}] {last_msg.content}")


if __name__ == "__main__":
    main()
