"""Graph assembly.

This module wires nodes and edges together into a compiled LangGraph.

Architecture — ReAct loop
--------------------------

    START
      │
      ▼
   [agent]  ──── (has tool calls?) ──── YES ──▶  [tools]
      ▲                                               │
      └───────────────────────────────────────────────┘
      │
     NO
      ▼
     END

- ``agent``:  calls the LLM; may request one or more tool calls.
- ``tools``:  ``ToolNode`` executes all requested tools in parallel,
              then returns ``ToolMessage`` results to the conversation.
- ``tools_condition`` (LangGraph prebuilt): routes to ``"tools"`` when the
  last AI message contains tool calls, otherwise routes to ``END``.

Usage
-----
Import the pre-compiled ``graph`` singleton for use in your application:

    from agent import graph
    result = graph.invoke(
        {"messages": [("human", "What is 2**10?")]},
        config={"configurable": {"thread_id": "1"}},
    )

Or call ``build_graph()`` to get a fresh compiled graph (useful in tests).
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.nodes import call_model
from agent.state import AgentState
from agent.tools.tools import get_tools


def build_graph() -> CompiledStateGraph:
    """Construct and compile the agent graph.

    Returns
    -------
    CompiledStateGraph
        A runnable LangGraph object.  Call ``.invoke()``, ``.stream()``,
        or ``.ainvoke()`` / ``.astream()`` on it.
    """
    tools = get_tools()

    builder = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))

    # ── Edges ──────────────────────────────────────────────────────────────
    builder.add_edge(START, "agent")

    # ``tools_condition`` checks for tool calls on the last AI message:
    #   - tool calls present  → route to "tools"
    #   - no tool calls       → route to END
    builder.add_conditional_edges("agent", tools_condition)

    # After tools execute, always loop back to the agent.
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=InMemorySaver())


# Module-level singleton — import this for normal use.
graph: CompiledStateGraph = build_graph()
