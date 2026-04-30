# coding_agent

A LangGraph ReAct (Reasoning + Acting) AI agent starter kit. The agent uses an agentic loop where an LLM decides whether to call tools, executes them, feeds results back, and repeats until it produces a final answer.

## Features

- **ReAct loop** built with LangGraph `StateGraph`
- **Multi-provider** support: OpenAI (default `gpt-4o`) and Anthropic (default `claude-3-5-sonnet-20241022`)
- **Built-in tools**: safe arithmetic evaluator, current UTC datetime
- **Optional web search** via Tavily (enabled when `TAVILY_API_KEY` is set)
- **Interactive CLI REPL** for multi-turn conversation
- **Streaming support**: both synchronous and async token-by-token
- **Full test suite** runnable without live API keys
- Strict typing with `mypy`, linting with `ruff`

## Requirements

- Python >= 3.11
- [`uv`](https://github.com/astral-sh/uv) package manager

## Installation

```bash
# Install uv if needed
pip install uv

# Create virtual environment and install all dependencies
uv sync --all-groups

# Copy and configure environment variables
cp .env.example .env
```

Edit `.env` and set at least one of `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

## Configuration

All configuration is via environment variables (loaded from `.env`):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `"openai"` or `"anthropic"` |
| `OPENAI_API_KEY` | — | Required for OpenAI |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `MODEL_NAME` | _(provider default)_ | Override model name (e.g. `gpt-4o-mini`) |
| `TEMPERATURE` | `0` | LLM sampling temperature |
| `TAVILY_API_KEY` | — | Optional; enables live web search tool |
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | — | LangSmith API key |
| `LANGCHAIN_PROJECT` | `agent-dev` | LangSmith project name |

## Usage

### Interactive CLI

```bash
uv run agent
```

Type messages to chat with the agent. Use `quit`, `exit`, or Ctrl-C to stop.

### Streaming example

```bash
uv run python examples/streaming.py
```

Demonstrates both synchronous (node-by-node updates) and async (token-by-token) streaming modes.

### As a library

```python
from agent import graph
from langchain_core.messages import HumanMessage

result = graph.invoke({"messages": [HumanMessage(content="What is 42 * 7?")]})
print(result["messages"][-1].content)
```

## Project Structure

```
src/agent/
├── __init__.py      # Public API: exports build_graph, graph
├── __main__.py      # Interactive CLI REPL entry point
├── config.py        # Settings from env vars; LLM factory
├── graph.py         # LangGraph StateGraph assembly
├── nodes.py         # Graph node functions (call_model)
├── state.py         # AgentState TypedDict
└── tools.py         # Tool definitions

tests/
└── test_agent.py    # Pytest suite (no API keys required)

examples/
└── streaming.py     # Sync and async streaming demos
```

## Graph Architecture

```
START → [agent] → (tool calls?) → YES → [tools] → [agent]
                       ↓ NO
                      END
```

The `agent` node prepends a system prompt and calls the LLM with all tools bound. The `tools` node executes any requested tool calls. Routing uses LangGraph's built-in `tools_condition`.

## Development

```bash
# Run tests (no API keys needed)
uv run pytest

# Run integration tests (requires live API keys)
uv run pytest -m integration

# Lint
uv run ruff check src tests

# Type check
uv run mypy src

# Build wheel
uv build
```

## Adding Tools

Define a new tool in `src/agent/tools.py` using the `@tool` decorator, then add it to the list returned by `get_tools()`. No other files need to change.

```python
from langchain_core.tools import tool

@tool
def my_tool(input: str) -> str:
    """Description of what this tool does."""
    return "result"
```
