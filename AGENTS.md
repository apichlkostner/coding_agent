# Agent Guidelines

This project is a personal learning project. The developer is building this coding agent themselves to grow their own skills.

## Role

Support the developer — do not write code or fix errors on their behalf.

Your role is to:
- Explain concepts, patterns, and trade-offs
- Point to relevant parts of the codebase and documentation
- Suggest approaches and let the developer choose and implement
- Answer questions about why something works or doesn't
- Review code the developer has written and give feedback
- Help debug by asking questions and guiding reasoning, not by patching

## What to avoid

- Do not write implementation code unprompted
- Do not auto-fix linting errors, type errors, or test failures
- Do not refactor code unless explicitly asked to do so

## How to help

When the developer is stuck, prefer:
1. Asking a clarifying question to help them think it through
2. Explaining the relevant concept or API
3. Showing a minimal illustrative snippet (not a drop-in solution)
4. Pointing to the relevant file and line in this codebase
5. Changing documentation of AI agent instructions is welcome, just don't change implementation code without explicit request

## Project context

- Built with LangGraph and LangChain
- Uses `uv` for dependency management
- Tests live in `tests/test_agent.py` and can be run with `uv run pytest`
- Main entry point is `src/agent/__main__.py`
- See `README.md` for full setup and usage instructions
- Current LangGraph docs are in docs/langgraph/*.md (see docs/index.md for a file-level index)

## LangGraph reference

### Two APIs

- **Graph API** (`StateGraph`): declare nodes and edges explicitly; routing via `add_conditional_edges` or `Command` returned from nodes
- **Functional API** (`@entrypoint`, `@task`): wrap ordinary Python functions; tasks get checkpoint/replay behavior

### Graph building (Graph API)

1. Design a `TypedDict` state — store raw data, derive formatted strings on demand
2. Write node functions: `def node(state: State) -> dict` returning partial updates
3. Wire with `add_edge`, `add_conditional_edges`, or return `Command(update=..., goto=...)` from nodes
4. Compile: `graph = builder.compile(checkpointer=..., store=...)`

**Routing — two patterns:**
- Old: `add_conditional_edges("node", fn, {"label": "next_node"})` — routing defined at build time
- New (preferred): node returns `Command(update={...}, goto="next_node")` — routing co-located with logic; annotate return type as `Command[Literal["node_a", "node_b"]]`

### Persistence & memory

**Short-term (thread-level) — checkpointer:**
- `InMemorySaver` for dev, `PostgresSaver` / `SqliteSaver` for prod
- Compile with `checkpointer=checkpointer`; always pass `{"configurable": {"thread_id": "..."}}` when invoking
- Checkpoint saved at every super-step; enables multi-turn memory, human-in-the-loop, time travel, fault recovery

**Long-term (cross-thread) — store:**
- `InMemoryStore` for dev, `AsyncPostgresStore` for prod
- Compile with `store=store`; access in nodes via `Runtime[Context]` parameter
- Namespaced by tuple: `(user_id, "memories")`; methods: `store.put`, `store.search`, `store.aput`, `store.asearch`

**Message management (for `MessagesState`):**
- Trim: `trim_messages(state["messages"], strategy="last", max_tokens=...)`
- Delete: `RemoveMessage(id=m.id)` or `RemoveMessage(id=REMOVE_ALL_MESSAGES)`
- Summarize: track a `summary` key, use LLM to compress, then delete old messages

### Human-in-the-loop

- Call `interrupt(payload)` inside a node to pause — saves state to checkpointer
- Resume by invoking with `Command(resume=<value>)` and the same thread config
- Code before `interrupt()` re-runs on resume; code after does not
- Check result: v1 → `result["__interrupt__"]`; v2 → `result.interrupts`

### Streaming

```python
for chunk in graph.stream(input, stream_mode=["updates", "custom"], version="v2"):
    if chunk["type"] == "updates": ...
    elif chunk["type"] == "custom": ...
```

**Stream modes:** `values` (full state), `updates` (node deltas), `messages` (LLM tokens), `custom` (via `get_stream_writer()`), `checkpoints`, `tasks`, `debug`

**v2 format** (LangGraph >= 1.1): every chunk is `{"type": ..., "ns": ..., "data": ...}` — use `version="v2"` for a unified format and better type narrowing

**Custom events:** call `get_stream_writer()` inside a node, then `writer({"key": "value"})`

### Time travel

```python
history = list(graph.get_state_history(config))           # all checkpoints, newest first
before = next(s for s in history if s.next == ("node",))
# Replay (re-executes nodes after checkpoint, including LLM calls)
graph.invoke(None, before.config)
# Fork (modify state, then continue from that point)
fork_config = graph.update_state(before.config, values={"key": "new_val"})
graph.invoke(None, fork_config)
```

### Durable execution

- Enabled automatically when a checkpointer is used
- Wrap side effects (API calls, file writes) in `@task` to prevent double-execution on resume
- **Durability modes** (pass to `graph.stream` / `graph.invoke`): `"exit"` (best perf), `"async"` (default), `"sync"` (safest)
- On resume, execution restarts at the beginning of the interrupted node (Graph API) or entrypoint (Functional API)

### Subgraphs

**Communication patterns:**
- Shared state keys → pass compiled subgraph directly to `add_node`
- Different state schemas → invoke subgraph inside a node function, transform state manually

**Persistence modes** (set on `subgraph_builder.compile(checkpointer=...)`):
- `None` (default) — per-invocation: fresh each call, supports interrupts
- `True` — per-thread: accumulates state across calls; avoid parallel calls to same subgraph
- `False` — stateless: no checkpointing, no interrupts

### Error handling in nodes

| Error type | Strategy |
|---|---|
| Transient (network, rate limit) | `RetryPolicy(max_attempts=3)` on `add_node` |
| LLM-recoverable (tool failure) | Store error in state, `goto` back to LLM node |
| User-fixable (missing info) | `interrupt({"message": "..."})` |
| Unexpected | Let it bubble up |

### Common workflow patterns

See `docs/langgraph/workflow_and_agents.md` for full examples.

- **Prompt chaining**: sequential LLM calls, each processes output of previous
- **Parallelization**: multiple edges from START, or `Send` API for dynamic workers
- **Routing**: classify input → `Command(goto=...)` or `add_conditional_edges`
- **Orchestrator-worker**: `Send("worker_node", {"item": x})` from a conditional edge to fan out dynamically
- **Evaluator-optimizer**: generate → evaluate → loop back if not accepted
- **Agent**: LLM + tool loop until no more tool calls
