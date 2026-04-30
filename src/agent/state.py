"""Agent state definition.

LangGraph passes this TypedDict between every node in the graph.
Add fields here as your agent grows (e.g. memory, scratchpad, metadata).
"""

from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """Shared state that flows through the graph.

    Attributes
    ----------
    messages:
        Conversation history.  The ``add_messages`` reducer *appends*
        new messages rather than overwriting the list, so nodes only need
        to return the delta: ``{"messages": [new_message]}``.
    """

    messages: Annotated[list, add_messages]
