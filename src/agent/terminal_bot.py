import asyncio
import sys

from agent.graph import graph
from langchain_core.messages import HumanMessage
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

# Each session gets its own thread so the checkpointer maintains history.
_CONFIG = {"configurable": {"thread_id": "cli-session"}}

class TerminalBot:
    def __init__(self):
        self.session = PromptSession(history=InMemoryHistory())
    
    async def start(self):
        """Run the agent in an interactive REPL loop."""
        print("LangGraph Coding Agent  (type 'quit' or Ctrl-C to exit)\n")

        while True:
            try:
                user_input = (await self.session.prompt_async("You: ")).strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye.")
                sys.exit(0)

            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "q"}:
                print("Goodbye.")
                return

            async for step in graph.astream(
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
