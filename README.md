# coding_agent

A LangGraph ReAct (Reasoning + Acting) AI agent specialised for coding tasks. The agent uses a tool-calling loop where an LLM decides whether to invoke tools, executes them, feeds results back, and repeats until it produces a final answer.

## Features

- **ReAct loop** built with LangGraph `StateGraph`
- **Multi-provider** support: OpenAI (default `gpt-5.4-nano`), Anthropic (default `claude-haiku-4-5`), and Ollama local models (default `qwen2.5-coder:14b`)
- **Built-in tools**: safe arithmetic evaluator, current UTC datetime
- **Filesystem tools**: read, write, list, create directory, replace-in-file, grep
- **Shell tool**: run arbitrary bash commands (see [Security](#security))
- **Optional web search** via Tavily (enabled when `TAVILY_API_KEY` is set)
- **Message router**: clean adapter abstraction — terminal REPL, Discord bot, and periodic heartbeat run concurrently through a single router
- **Heartbeat**: periodic agent-initiated runs driven by a Markdown prompt file; output can be forwarded to any adapter (e.g. a Discord monitoring channel)
- **Per-session memory**: conversations persisted per thread via `InMemorySaver`
- **Full test suite** runnable without live API keys

## Requirements

- Python >= 3.11
- [`uv`](https://github.com/astral-sh/uv) package manager

## Installation

```bash
# Install uv if needed
pip install uv

# Create virtual environment and install all dependencies
uv sync --all-groups

# Copy and fill in your environment variables
cp .env.example .env
```

Edit `.env` and configure your provider:
- OpenAI: set `OPENAI_API_KEY`
- Anthropic: set `ANTHROPIC_API_KEY`
- Ollama: set `LLM_PROVIDER=ollama` and ensure Ollama is running (default `OLLAMA_BASE_URL=http://localhost:11434`)

## Configuration

All settings are loaded from environment variables (`.env` is read automatically via `python-dotenv`).

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `"openai"`, `"anthropic"`, or `"ollama"` |
| `OPENAI_API_KEY` | — | Required for OpenAI |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL for Ollama server (used when `LLM_PROVIDER=ollama`) |
| `MODEL_NAME` | _(provider default)_ | Override model (e.g. `gpt-5.4-mini` or `qwen2.5-coder:14b`) |
| `TEMPERATURE` | `0` | LLM sampling temperature |
| `ENABLED_ADAPTERS` | `terminal,discord,heartbeat` | Comma-separated adapters to start; unset = all three built-in; `""` = none. Add `matrix` to enable the Matrix adapter. |
| `DISCORD_BOT_TOKEN` | — | Discord bot token; adapter skipped if absent |
| `HEARTBEAT_INTERVAL_SECONDS` | `600` | Seconds between heartbeat runs |
| `HEARTBEAT_PROMPT_FILE` | `HEARTBEAT.md` | Path to the prompt file sent to the agent each tick |
| `HEARTBEAT_OUTPUT_ADAPTER` | — | Adapter to forward heartbeat responses to (e.g. `discord`) |
| `HEARTBEAT_OUTPUT_CHANNEL` | — | Destination within that adapter (e.g. a Discord channel ID) |
| `MATRIX_HOMESERVER_URL` | — | Matrix homeserver base URL (e.g. `https://matrix.org`) |
| `MATRIX_ACCESS_TOKEN` | — | Bot access token; adapter skipped if any Matrix var is absent |
| `MATRIX_USER_ID` | — | Fully-qualified bot user ID (e.g. `@bot:matrix.org`) |
| `MATRIX_DEVICE_ID` | — | Optional Matrix device ID for persisted encrypted sessions |
| `MATRIX_STORE_PATH` | — | Optional `matrix-nio` store directory for E2EE state and sync tokens |
| `MATRIX_IGNORE_UNVERIFIED_DEVICES` | `true` | When sending into encrypted rooms, allow delivery to proceed even if devices are unverified |
| `TAVILY_API_KEY` | — | Enables live web search tool |
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | — | LangSmith API key |
| `LANGCHAIN_PROJECT` | `agent-dev` | LangSmith project name |

## Usage

### Start the agent

#### Example: run with a local Ollama model

```ini
# .env
LLM_PROVIDER=ollama
MODEL_NAME=qwen2.5-coder:14b
OLLAMA_BASE_URL=http://localhost:11434
```

> **Note:** Tool-calling reliability varies between local models. Prefer instruction-tuned/chat models with strong tool-use support.

```bash
uv run agent
```

This starts all enabled adapters concurrently inside a single process:

- **Terminal REPL** — type messages directly in the terminal. The agent prints `[node] response` as steps complete. Type `quit`, `exit`, `q`, or press Ctrl-D to stop.
- **Discord bot** — if `DISCORD_BOT_TOKEN` is set, the bot comes online and responds to messages in any channel it can read. A "typing…" indicator is shown while the agent works.
- **Heartbeat** — reads `HEARTBEAT.md` (configurable) and runs the agent on that prompt every N seconds. Output is written to `agent.log`. Optionally forwards responses to any registered adapter.

All interfaces share the same agent graph and per-session conversation memory. Each source gets its own LangGraph thread ID so histories are kept isolated.

### Sandboxed execution

Because the agent can run shell commands, it is recommended to run inside a sandbox in production:

```bash
bash start_sandboxed.sh
```

See [Security](#security) for details.

## Adapter Setup

### Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, create a bot user and copy the token into `DISCORD_BOT_TOKEN` in `.env`.
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**.
4. Under **OAuth2 → URL Generator**, select the `bot` scope and the `Send Messages` + `Read Message History` permissions. Open the generated URL to invite the bot to your server.
5. Start the agent — the bot will come online and reply to every message in channels it can access.

Each user × channel combination gets its own persistent conversation thread (thread ID: `discord-{user_id}-{channel_id}`).

### Matrix

1. Create a bot account on your homeserver (via the homeserver's registration page or admin API).
2. Obtain an access token:
   ```bash
   curl -XPOST 'https://<homeserver>/_matrix/client/v3/login' \
        -H 'Content-Type: application/json' \
        -d '{"type":"m.login.password","user":"<username>","password":"<password>"}'
   ```
   Copy the `access_token` from the response.
3. Set the three env vars in `.env`:
   ```ini
   MATRIX_HOMESERVER_URL=https://<homeserver>
   MATRIX_ACCESS_TOKEN=<token from step 2>
   MATRIX_USER_ID=@<username>:<homeserver>
   ```
    For encrypted rooms, also set a stable device and store path:
    ```ini
    MATRIX_DEVICE_ID=<existing or new device id>
    MATRIX_STORE_PATH=./nio_store
    ```
4. Add `matrix` to `ENABLED_ADAPTERS`:
   ```ini
   ENABLED_ADAPTERS=terminal,discord,heartbeat,matrix
   ```
5. Add the bot to rooms manually using an admin account or Element. The bot does **not** auto-accept invitations.
6. Start the agent — the bot will respond to every text message in all joined rooms.

On startup, the Matrix adapter performs an initial sync before registering callbacks so it skips old backlog, then continues incremental syncs using the stored sync token when a store path is configured.

Each user × room combination gets its own persistent conversation thread (thread ID: `matrix-{room_id}-{sender_id}`). Replies are threaded using Matrix’s `m.in_reply_to` so conversations stay readable in shared rooms.

### Heartbeat

The heartbeat runs the agent on a schedule without any human input. Create a `HEARTBEAT.md` in the project root:

```markdown
Check the project status. Look at recent git commits, open TODOs, and report
anything that needs attention. Be concise.
```

Then configure the interval and, optionally, where to send the results:

```ini
# .env
HEARTBEAT_INTERVAL_SECONDS=600     # run every 10 minutes
HEARTBEAT_PROMPT_FILE=HEARTBEAT.md

# Forward the agent's response to a Discord channel:
HEARTBEAT_OUTPUT_ADAPTER=discord
HEARTBEAT_OUTPUT_CHANNEL=1234567890123456789  # right-click a channel → Copy Channel ID
```

When `HEARTBEAT_OUTPUT_ADAPTER` and `HEARTBEAT_OUTPUT_CHANNEL` are both set, the heartbeat response is posted to that adapter in addition to being logged. If neither is set, output goes only to `agent.log`.

> **Note:** `HEARTBEAT_OUTPUT_ADAPTER` must match the `adapter_id` of a registered adapter (`"discord"`, `"terminal"`, or any future adapter). The Discord adapter must also be enabled and have a valid token.

## Project Structure

```
src/agent/
├── __init__.py              # Public API: exports build_graph, graph
├── __main__.py              # Entry point: build_router(), main()
├── config.py                # Settings dataclasses; LLM factory
├── graph.py                 # LangGraph StateGraph assembly
├── nodes.py                 # Graph node functions (call_model)
├── state.py                 # AgentState TypedDict
├── tools.py                 # Core tools (calculate, datetime, web search)
├── tools_filesystem.py      # Filesystem tools (read/write/list/grep/replace)
├── tools_cmd.py             # Shell tool (bash)
│
├── router/                  # Routing layer
│   ├── messages.py          # InboundMessage / OutboundMessage dataclasses
│   ├── base_adapter.py      # BaseAdapter ABC
│   ├── agent_service.py     # AgentService — owns the LangGraph astream loop
│   └── router.py            # MessageRouter — hub connecting adapters ↔ agent
│
└── adapters/                # Channel implementations
    ├── terminal_adapter.py  # Interactive REPL over stdin/stdout
    ├── discord_adapter.py   # discord.py bot
    ├── heartbeat_adapter.py # Periodic scheduled runs
    └── matrix_adapter.py   # matrix-nio bot

tests/
├── test_agent.py            # Tools, config, graph structure tests
├── test_router.py           # Router and AgentService unit tests
├── test_adapters.py         # Adapter unit tests
└── test_main.py             # build_router() and integration tests

examples/
└── streaming.py             # Sync and async streaming demos
```

## Architecture

```
                         ┌─────────────────────────┐
                         │       __main__.py        │
                         │   build_router(settings) │
                         └────────────┬────────────┘
                                      │ registers
          ┌───────────────────────────┼───────────────────────────┬──────────────────┐
          │                           │                           │                  │
   TerminalAdapter            DiscordAdapter             HeartbeatAdapter    MatrixAdapter
   (stdin/stdout REPL)        (discord.py bot)           (scheduled trigger) (matrix-nio bot)
          │                           │                           │                  │
          └───────────────────────────┼───────────────────────────┴──────────────────┘
                                      │ InboundMessage
                         ┌────────────▼────────────┐
                         │      MessageRouter       │
                         │  per-thread asyncio.Lock │
                         └────────────┬────────────┘
                                      │
                         ┌────────────▼────────────┐
                         │      AgentService        │
                         │   graph.astream(...)     │
                         └────────────┬────────────┘
                                      │ OutboundMessage(s)
                         ┌────────────▼────────────┐
                         │      MessageRouter       │
                         │  routes to adapter.send()│
                         └─────────────────────────┘
```

**Key properties:**
- Each adapter translates platform events into `InboundMessage` and `OutboundMessage`; the router and agent service never touch platform-specific APIs.
- Messages on the **same thread ID** are serialised (one `asyncio.Lock` per thread) to prevent LangGraph checkpointer races. Messages on different threads run concurrently.
- `HeartbeatAdapter` is agent-*initiated*: it injects `InboundMessage` objects on a schedule rather than waiting for user input.

## Graph Architecture

```
START → [agent] → (has tool calls?) → YES → [tools] → back to [agent]
                         ↓ NO
                        END
```

The `agent` node prepends a system prompt (with today's date) and calls the LLM with all tools bound. The `tools` node runs requested tool calls in parallel. Routing uses LangGraph's built-in `tools_condition`.

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
| `treesitter_parse` | `tools_treesitter.py` | Parse a source file or code string into a JSON syntax tree (bounded by depth and character limit) |
| `treesitter_query` | `tools_treesitter.py` | Run a tree-sitter S-expression query against a source file or code string; returns matched captures as JSON |
| `treesitter_get_symbols` | `tools_treesitter.py` | Extract top-level symbols (functions, classes, imports) from a source file using built-in per-language queries |

All filesystem and shell tools restrict access to paths inside the project working directory.

## Security

The `bash` tool executes arbitrary shell commands with the privileges of the running process. This is intentional for a coding agent use-case, but **the agent must not be exposed to untrusted input without a sandbox**.

Current protection: all filesystem tools enforce that paths stay within the project root (via `_is_subpath`). The `bash` tool has no such restriction beyond the OS-level sandbox.

Recommended mitigation: run through `start_sandboxed.sh`, which uses [firejail](https://firejail.wordpress.com/) to confine the process to a whitelist of paths:

```bash
bash start_sandboxed.sh
```

> **Known limitation:** the security model is minimal and needs improvement. Future work should include a proper permission system (allowlist of commands/paths), stronger sandbox configuration, and human-in-the-loop confirmation for destructive operations.

## Development

```bash
# Run tests (no API keys needed)
uv run pytest

# Run only integration tests (requires live API keys)
uv run pytest -m integration

# Lint
uv run ruff check src tests

# Type check
uv run mypy src

# Build wheel
uv build
```

## Adding an Adapter

Create a class that inherits from `BaseAdapter` and implements two methods:

```python
from agent.router.base_adapter import BaseAdapter
from agent.router.messages import InboundMessage, OutboundMessage
from agent.router.router import MessageRouter

class MyAdapter(BaseAdapter):
    adapter_id = "my_channel"          # must be unique

    async def start(self, router: MessageRouter) -> None:
        # subscribe to events; call router.dispatch() for each inbound event
        async for event in my_platform.listen():
            inbound = InboundMessage(
                adapter_id=self.adapter_id,
                thread_id=f"my_channel-{event.user_id}",
                content=event.text,
                reply_channel_id=str(event.channel_id),
                user_id=str(event.user_id),
            )
            await router.dispatch(inbound)   # fire-and-forget

    async def send(self, message: OutboundMessage) -> None:
        # deliver message.content to message.reply_channel_id
        # use message.msg_type to decide formatting
        if message.msg_type in ("response", "error"):
            await my_platform.send(message.reply_channel_id, message.content)
```

Then register it in `__main__.py` (or pass it to `build_router()` in tests):

```python
router.register(MyAdapter())
```

No other files need to change.

## Adding Tools

Define a new tool using the `@tool` decorator in the appropriate module, then include it in the list returned by `get_tools()` in `tools.py`:

```python
from langchain_core.tools import tool

@tool
def my_tool(input: str) -> str:
    """Description of what this tool does."""
    return "result"
```

## As a Library

```python
from agent import graph
from langchain_core.messages import HumanMessage

result = graph.invoke(
    {"messages": [HumanMessage(content="What is 42 * 7?")]},
    config={"configurable": {"thread_id": "my-session"}},
)
print(result["messages"][-1].content)
```
