"""router package — message routing layer between adapters and the agent."""

from agent.router.agent_service import AgentService
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, MessageType, OutboundMessage
from agent.router.router import MessageRouter

__all__ = [
    "AgentService",
    "BaseAdapter",
    "InboundMessage",
    "MessageType",
    "OutboundMessage",
    "MessageRouter",
]
