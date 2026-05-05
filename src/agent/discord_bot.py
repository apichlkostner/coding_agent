# discord_bot.py
import discord
from langchain_core.messages import HumanMessage

from agent.graph import graph

intents = discord.Intents.default()
intents.message_content = True 

class DiscordBot(discord.Client):
    def get_thread_id(self, user_id: int, channel_id: int) -> str:
        """Create a unique thread ID for conversation persistence per user per channel."""
        return f"discord-{user_id}-{channel_id}"


    async def stream_agent_response(self, channel: discord.TextChannel, user_id: int, channel_id: int, user_message: str) -> None:
        """Invoke the agent and stream responses to Discord."""
        thread_id = self.get_thread_id(user_id, channel_id)
        config = {"configurable": {"thread_id": thread_id}}

        buffer = ""

        try:
            async with channel.typing():
                async for step in graph.astream(
                    {"messages": [HumanMessage(content=user_message)]},
                    stream_mode="updates",
                    config=config,
                ):
                    node_name, node_output = next(iter(step.items()))
                    last_msg = node_output["messages"][-1]

                    # Handle tool calls
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        calls = ", ".join(
                            tc["name"] + "(" + str(tc["args"])[:50] + ")"
                            for tc in last_msg.tool_calls
                        )
                        buffer += f"\n**Tools:** {calls}"

                    # Handle tool results
                    elif hasattr(last_msg, "name") and last_msg.name is not None:
                        result_preview = last_msg.content[:200]
                        if len(last_msg.content) > 200:
                            result_preview += "..."
                        buffer += f"\n**Result:** {result_preview}"

                    # Handle agent response
                    else:
                        buffer = last_msg.content

            # Send response (split if too long)
            if buffer:
                for i in range(0, len(buffer), 2000):
                    chunk = buffer[i : i + 2000]
                    await channel.send(chunk)
        except Exception as e:
            await channel.send(f"Error: {str(e)[:200]}")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")


    async def on_message(self, message):
        if message.author.bot:
            return

        print(f"{message.author}: {message.content}")
        await self.stream_agent_response(message.channel, message.author.id, message.channel.id, message.content)
