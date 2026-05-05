# coding_agent

A LangGraph ReAct (Reasoning + Acting) AI agent, specialized on coding tasks. The agent uses an agentic loop where an LLM decides whether to call tools, executes them, feeds results back, and repeats until it produces a final answer.

## Features

- **ReAct loop** built with LangGraph `StateGraph`
- **Multi-provider** support: OpenAI (default `gpt-5.4-nano`) and Anthropic (default `claude-haiku-4-5`)
- **Built-in tools**: safe arithmetic evaluator, current UTC datetime
- **Filesystem tools**: read, write, list, create directory, replace-in-file, grep
- **Shell tool**: run arbitrary bash commands (see [Security](#security))
- **Optional web search** via Tavily (enabled when `TAVILY_API_KEY` is set)
- **Two chat interfaces**: interactive terminal REPL and Discord bot, running concurrently
- **Streaming support**: async token-by-token streaming in both interfaces
- **Per-session memory**: conversations are persisted per thread via `InMemorySaver`
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

Edit `.env` and set at least one of `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Set `DISCORD_TOKEN` if you want to run the Discord bot.

## Configuration

All configuration is via environment variables (loaded from `.env`):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `"openai"` or `"anthropic"` |
| `OPENAI_API_KEY` | — | Required for OpenAI |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `MODEL_NAME` | _(provider default)_ | Override model name (e.g. `gpt-4o-mini`) |
| `TEMPERATURE` | `0` | LLM sampling temperature |
| `DISCORD_TOKEN` | — | Required for Discord bot; omit to run terminal only |
| `TAVILY_API_KEY` | — | Optional; enables live web search tool |
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | — | LangSmith API key |
| `LANGCHAIN_PROJECT` | `agent-dev` | LangSmith project name |

## Usage

### Start the agent

```bash
uv run agent
```

This starts both interfaces concurrently:

- **Terminal REPL** — type messages directly in the terminal. Use `quit`, `exit`, or Ctrl-C to stop.
- **Discord bot** — if `DISCORD_TOKEN` is set, the bot comes online and responds to messages in any channel it can read.

Each user/channel combination in Discord gets its own conversation thread (memory is kept per session via `InMemorySaver`).

### Sandboxed execution

Because the agent can run shell commands, it is recommended to run it inside a sandbox in production:

```bash
bash start_sandboxed.sh
```

See [Security](#security) for details.

### Discord bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, create a bot user and copy the token into `DISCORD_TOKEN` in your `.env`.
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**.
4. Under **OAuth2 → URL Generator**, select the `bot` scope and the `Send Messages` + `Read Message History` permissions. Open the generated URL to invite the bot to your server.
5. Start the agent — the bot will come online and reply to every message in channels it can access.

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
├── __init__.py          # Public API: exports build_graph, graph
├── __main__.py          # Entry point: starts terminal + Discord bots concurrently
├── config.py            # Settings from env vars; LLM factory
├── graph.py             # LangGraph StateGraph assembly
├── nodes.py             # Graph node functions (call_model)
├── state.py             # AgentState TypedDict
├── tools.py             # Core tool definitions (calculate, datetime, web search)
├── tools_filesystem.py  # Filesystem tools (read/write/list/grep/replace)
├── tools_cmd.py         # Shell tool (bash)
├── terminal_bot.py      # Interactive terminal REPL
└── discord_bot.py       # Discord bot interface

tests/
└── test_agent.py        # Pytest suite (no API keys required)

examples/
└── streaming.py         # Sync and async streaming demos
```

## Graph Architecture

```
START → [agent] → (tool calls?) → YES → [tools] → [agent]
                       ↓ NO
                      END
```

The `agent` node prepends a system prompt and calls the LLM with all tools bound. The `tools` node executes any requested tool calls in parallel. Routing uses LangGraph's built-in `tools_condition`.

## Tools

| Tool | Module | Description |
|---|---|---|
| `calculate` | `tools.py` | Safe AST-based arithmetic evaluator |
| `get_current_datetime` | `tools.py` | Current UTC time as ISO-8601 |
| `web_search` | `tools.py` | Tavily web search (requires `TAVILY_API_KEY`) |
| `read_file` | `tools_filesystem.py` | Read a file with optional line offset/count |
| `write_file` | `tools_filesystem.py` | Write (or overwrite) a file |
| `list_directory` | `tools_filesystem.py` | List directory contents with entry types |
| `create_directory` | `tools_filesystem.py` | Create a directory (mkdir -p) |
| `replace_in_file` | `tools_filesystem.py` | Replace a string inside a file |
| `grep` | `tools_filesystem.py` | Regex search across files in a directory |
| `bash` | `tools_cmd.py` | Run an arbitrary shell command |

All filesystem and shell tools restrict access to paths inside the project working directory.

## Security

The `bash` tool executes arbitrary shell commands with the privileges of the running process. This is intentional for a coding agent use-case, but it means **the agent must not be exposed to untrusted input without a sandbox**.

Current protection: all filesystem tools enforce that paths stay within the project root (via `_is_subpath`). The `bash` tool has no such restriction beyond the OS-level sandbox.

Recommended mitigation: run the agent through `start_sandboxed.sh`, which uses [firejail](https://firejail.wordpress.com/) to confine the process to a whitelist of paths:

```bash
bash start_sandboxed.sh
```

> **Known limitation:** the security model is minimal and needs improvement. Future work should include a proper permission system (allowlist of commands/paths), stronger sandbox configuration, and human-in-the-loop confirmation for destructive operations.

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

Define a new tool in the appropriate module (`tools.py`, `tools_filesystem.py`, or `tools_cmd.py`) using the `@tool` decorator, then ensure it is imported and included in the list returned by `get_tools()` in `tools.py`. No other files need to change.

```python
from langchain_core.tools import tool

@tool
def my_tool(input: str) -> str:
    """Description of what this tool does."""
    return "result"
```
