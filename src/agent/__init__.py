"""LangGraph ReAct agent — public API."""

# Load .env before any submodule is imported: ``graph`` is built at import
# time and ``get_tools()`` reads ``TAVILY_API_KEY`` from the environment.
#
# The agent may run with an arbitrary project directory as its working
# directory, so ``load_dotenv()`` (which searches upward from the CWD) is not
# sufficient.  Load the agent repository's own ``.env`` explicitly first, then
# fall back to CWD discovery for direct ``python -m agent`` runs.
from pathlib import Path

from dotenv import load_dotenv

_AGENT_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_AGENT_REPO_ROOT / ".env")
load_dotenv()

from agent.graph import build_graph, graph  # noqa: E402

__all__ = ["build_graph", "graph"]
