"""adapters package — channel adapters that implement BaseAdapter."""

from agent.adapters.discord_adapter import DiscordAdapter
from agent.adapters.heartbeat_adapter import HeartbeatAdapter
from agent.adapters.terminal_adapter import TerminalAdapter

__all__ = ["DiscordAdapter", "HeartbeatAdapter", "TerminalAdapter"]
