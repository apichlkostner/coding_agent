"""Graph nodes.

Each function here is a *node* in the LangGraph ``StateGraph``.  A node
receives the full ``AgentState`` and returns a *partial* state dict —
LangGraph merges the delta back using the reducers defined on each field.

Nodes in this file
------------------
call_model
    Prepends the system prompt (once), then invokes the LLM.
    Returns ``{"messages": [ai_message]}`` — the ``add_messages`` reducer
    appends it to the conversation history.
"""

from __future__ import annotations

from functools import cache

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from agent.config import get_llm, get_settings
from agent.state import AgentState
from agent.tools import get_tools

# ---------------------------------------------------------------------------
# System prompt — edit this to change the agent's persona and behaviour.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a helpful AI assistant with access to tools.

Guidelines:
- Think step by step before deciding whether to use a tool.
- When you use a tool, explain briefly why you chose it.
- After receiving tool results, synthesise the information into a clear answer.
- Be concise. Avoid unnecessary filler text.
"""


# ---------------------------------------------------------------------------
# Lazy LLM initialisation (avoids import-time API-key checks in tests)
# ---------------------------------------------------------------------------


@cache
def _get_llm_with_tools() -> BaseChatModel:
    """Return the LLM with tools bound (cached singleton)."""
    settings = get_settings()
    llm = get_llm(settings)
    tools = get_tools()
    return llm.bind_tools(tools)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def call_model(state: AgentState) -> dict:  # type: ignore[type-arg]
    """Invoke the LLM with the current message history.

    If the first message in history is not already a ``SystemMessage``,
    one is prepended so the model always has its instructions.

    Parameters
    ----------
    state:
        Current graph state containing the conversation ``messages``.

    Returns
    -------
    dict
        ``{"messages": [ai_response]}`` — appended by the ``add_messages``
        reducer.
    """
    messages = list(state["messages"])

    # Prepend system prompt exactly once.
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]

    response = _get_llm_with_tools().invoke(messages)
    return {"messages": [response]}
