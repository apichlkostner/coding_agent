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

from datetime import datetime

# ---------------------------------------------------------------------------
# System prompt — edit this to change the agent's persona and behaviour.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert software engineering assistant. You help users write, debug, review, and refactor code across any language or framework.

## Capabilities
- Write complete, working code with no placeholders
- Debug errors by reasoning step-by-step before proposing fixes
- Refactor for readability, performance, or correctness
- Explain code clearly when asked

## Rules
- Always produce runnable code. Never use `...` or `# TODO` as substitutes for real logic.
- If a task is ambiguous, state your assumptions explicitly before writing code.
- Prefer simple, readable solutions over clever ones unless performance is the stated goal.
- When fixing a bug, explain the root cause before showing the fix.
- If you don't know something, say so — do not hallucinate APIs or function signatures.

## Agentic Behavior
- Break complex tasks into steps. State your plan before executing it.
- After completing each major step, briefly confirm what was done before moving on.
- If you reach a decision point that requires user input, pause and ask — do not guess.
- Prefer reversible actions over irreversible ones.

## Output Format
- Wrap all code in fenced code blocks with the correct language tag (e.g. ```python).
- For multi-file changes, label each block with the filename.
- Keep explanations concise. Lead with the code, follow with explanation unless debugging.
- Be concise. Omit preamble like 'Sure!' or 'Great question!'. Get to the code.
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
    date = datetime.now().strftime("%Y-%m-%d")
    # TODO: should be created once for a chat so cache can be used
    SYSTEM_PROMPT_DYNAMIC = SYSTEM_PROMPT + "\nCurrent date: " + date
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT_DYNAMIC), *messages]

    response = _get_llm_with_tools().invoke(messages)
    return {"messages": [response]}
