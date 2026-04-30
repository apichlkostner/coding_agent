"""CLI entry point — ``uv run agent`` or ``python -m agent``."""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage

from agent.graph import graph


def main() -> None:
    """Run the agent in an interactive REPL loop."""
    print("LangGraph ReAct Agent  (type 'quit' or Ctrl-C to exit)\n")
    history: list = []

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

        history.append(HumanMessage(content=user_input))

        result = graph.invoke({"messages": history})
        history = result["messages"]

        ai_message = history[-1]
        print(f"\nAgent: {ai_message.content}\n")


if __name__ == "__main__":
    main()
