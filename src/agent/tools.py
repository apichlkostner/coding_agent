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

import ast
import operator
import os
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, tool
from agent.tools_filesystem import *


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------


@tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result as a string.

    Supports: +, -, *, /, //, %, ** and parentheses.
    Does NOT execute arbitrary code — only numeric literals and operators
    are allowed (safe AST evaluation).

    Examples
    --------
    calculate("2 ** 10")        -> "1024"
    calculate("(3 + 4) * 6")    -> "42"
    """
    _OPERATORS: dict[type, object] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.expr) -> float:
        match node:
            case ast.Constant(value=v) if isinstance(v, int | float):
                return float(v)
            case ast.BinOp(left=left, op=op, right=right):
                op_fn = _OPERATORS.get(type(op))
                if op_fn is None:
                    raise ValueError(f"Unsupported operator: {type(op).__name__}")
                return op_fn(_eval(left), _eval(right))  # type: ignore[operator]
            case ast.UnaryOp(op=op, operand=operand):
                op_fn = _OPERATORS.get(type(op))
                if op_fn is None:
                    raise ValueError(f"Unsupported operator: {type(op).__name__}")
                return op_fn(_eval(operand))  # type: ignore[operator]
            case _:
                raise ValueError(f"Unsupported expression node: {ast.dump(node)}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        # Return int string when result is a whole number (e.g. "42" not "42.0")
        return str(int(result)) if result == int(result) else str(result)
    except Exception as exc:
        return f"Error: {exc}"


@tool
def get_current_datetime() -> str:
    """Return the current UTC date and time as an ISO-8601 string.

    Example
    -------
    get_current_datetime() -> "2025-04-30T12:00:00+00:00"
    """
    return datetime.now(tz=timezone.utc).isoformat()


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
    tools: list[BaseTool] = [calculate, get_current_datetime, read_file, write_file, list_directory, grep]

    web_search = _make_web_search_tool()
    if web_search:
        tools.append(web_search)

    return tools
