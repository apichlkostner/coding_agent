"""CLI entry point — ``uv run agent`` or ``python -m agent``."""

from __future__ import annotations
import asyncio
from agent.discord_bot import DiscordBot, intents
from agent.terminal_bot import TerminalBot
from agent.config import get_settings

async def main() -> None:
    settings = get_settings()

    discord_bot = DiscordBot(intents=intents)
    discord_task = asyncio.create_task(discord_bot.start(settings.discord_token))


    terminal_bot = TerminalBot()
    terminal_task = asyncio.create_task(terminal_bot.start())
    await asyncio.gather(discord_task, terminal_task)


if __name__ == "__main__":
    asyncio.run(main())
