"""LangGraph ReAct agent — public API."""

# Load .env before any submodule is imported: ``graph`` is built at import
# time and ``get_tools()`` reads ``TAVILY_API_KEY`` from the environment.
from dotenv import load_dotenv

load_dotenv()

from agent.graph import build_graph, graph  # noqa: E402

__all__ = ["build_graph", "graph"]
