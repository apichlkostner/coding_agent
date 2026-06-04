"""Tool definitions available to the agent.

Each tool is a plain Python function decorated with ``@tool`` from LangChain.
LangGraph's ``ToolNode`` discovers and executes them automatically.

Adding a new tool
-----------------
1. Define a function and decorate it with ``@tool``.
2. Add it to the list returned by ``get_tools()``.
3. That's it — the agent will be able to call it on the next run.

Built-in tools
--------------
- ``calculate``      — safe arithmetic evaluator (no external deps)
- ``get_current_datetime`` — returns the current UTC datetime

Optional tools (enabled when the matching API key is set)
---------------------------------------------------------
- ``web_search``     — live web search via Tavily (needs TAVILY_API_KEY)
"""

from __future__ import annotations

import os

from langchain_core.tools import BaseTool

from . import (
    bash,
    calculate,
    clangd_call_hierarchy,
    clangd_completion,
    clangd_definition,
    clangd_document_symbols,
    clangd_references,
    clangd_rename,
    clangd_type_hierarchy,
    clangd_workspace_symbols,
    create_directory,
    get_current_datetime,
    grep,
    list_directory,
    read_file,
    read_memory,
    replace_in_file,
    send_notification,
    store_memory,
    treesitter_get_symbols,
    treesitter_parse,
    treesitter_query,
    write_file,
)

# ---------------------------------------------------------------------------
# Optional tools
# ---------------------------------------------------------------------------


def _make_web_search_tool() -> BaseTool | None:
    """Return a Tavily web-search tool if TAVILY_API_KEY is configured."""
    if not os.getenv("TAVILY_API_KEY"):
        return None
    try:
        from langchain_tavily import TavilySearch  # noqa: PLC0415

        return TavilySearch(max_results=5)
    except ImportError:
        # langchain-tavily not installed — silently skip.
        return None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def get_tools() -> list[BaseTool]:
    """Return the list of tools available to the agent at runtime."""
    tools: list[BaseTool] = [
        calculate,
        get_current_datetime,
        read_file,
        write_file,
        list_directory,
        grep,
        replace_in_file,
        create_directory,
        bash,
        read_memory,
        send_notification,
        store_memory,
        treesitter_parse,
        treesitter_query,
        treesitter_get_symbols,
        # Language Server Protocol tools (require clangd on PATH).
        # Listed lazily — the underlying singleton is started on first
        # tool invocation, not at graph build time.
        clangd_completion,
        clangd_definition,
        clangd_references,
        clangd_document_symbols,
        clangd_workspace_symbols,
        clangd_rename,
        clangd_type_hierarchy,
        clangd_call_hierarchy,
    ]

    web_search = _make_web_search_tool()
    if web_search:
        tools.append(web_search)

    return tools
