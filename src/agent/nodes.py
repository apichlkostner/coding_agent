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
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import SecretStr

from agent.config import Config, load_config
from agent.prompts import PromptBuilder
from agent.state import AgentState
from agent.tools.tools import get_tools

# ---------------------------------------------------------------------------
# Lazy LLM initialisation (avoids import-time API-key checks in tests)
# ---------------------------------------------------------------------------


def get_llm_from_config(config: Config) -> BaseChatModel:
    """Instantiate and return the chat model described by a :class:`Config`.

    Parameters
    ----------
    config:
        Validated configuration loaded via
        :func:`agent.config.config.load_config`.

    Returns
    -------
    BaseChatModel
        A chat model wired to the provider declared in
        ``config.model_provider``.

    Raises
    ------
    ValueError
        If ``config.model_provider.api`` is not a supported wire protocol.
    """
    provider = config.model_provider
    model = config.model.name

    if provider.api == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        return ChatAnthropic(
            model=model,
            api_key=SecretStr(provider.api_key),
            base_url=provider.endpoint,
        )

    if provider.api in {"openai", "openai_compatible"}:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        return ChatOpenAI(
            model=model,
            api_key=SecretStr(provider.api_key),
            base_url=provider.endpoint,
        )

    raise ValueError(
        f"Unsupported provider api: {provider.api}. "
        "Expected one of: openai, openai_compatible, anthropic."
    )


@cache
def _get_llm_with_tools() -> Any:
    """Return the LLM with tools bound (cached singleton)."""
    config = load_config("config/config.yaml")
    llm = get_llm_from_config(config)
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
        messages = [SystemMessage(content=PromptBuilder().build()), *messages]

    response = _get_llm_with_tools().invoke(messages)
    return {"messages": [response]}
