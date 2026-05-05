import asyncio
import sys
import logging

from agent.graph import graph
from langchain_core.messages import HumanMessage
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

class HeartBeatBot:
    """HearBeatBot follows instructions from HEARTBEAT.md every 10 minutes"""
    def __init__(self):
        self.session = PromptSession(history=InMemoryHistory())
        self.CONFIG = {"configurable": {"thread_id": "heartbeat"}}
    
    async def start(self):
        heartbeat_prompt = ""
        try:
            heartbeat_prompt = Path("HEARTBEAT.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            logging.error(f"Error: HeartBeatBot, HEARTBEAT.md not found. Stopping heartbeat.")
            return

        while True:
            async for step in graph.astream(
                {"messages": [HumanMessage(content=heartbeat_prompt)]},
                stream_mode = "updates",
                config = self.CONFIG,
            ):
                node_name, node_output = next(iter(step.items()))
                last_msg = node_output["messages"][-1]

                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    calls = ", ".join(tc["name"]+"("+str(tc["args"])+")" for tc in last_msg.tool_calls)
                    logging.info(f"[{node_name}] → tool calls: {calls}")
                elif hasattr(last_msg, "name") and last_msg.name != None:  # ToolMessage
                    logging.info(f"[{node_name}] ← tool result: {last_msg.content[:180]}")
                else:
                    logging.info(f"[{node_name}] {last_msg.content}")
            # TODO: configure sleep time
            await asyncio.sleep(600)
