# coding_agent

A LangGraph ReAct (Reasoning + Acting) AI agent specialised for coding tasks. The agent uses a tool-calling loop where an LLM decides whether to invoke tools, executes them, feeds results back, and repeats until it produces a final answer.

## Features

- **ReAct loop** built with LangGraph `StateGraph`
- **Multi-provider** support: OpenAI (default `gpt-5.4-nano`), Anthropic (default `claude-haiku-4-5`), and Ollama local models (default `qwen2.5-coder:14b`)
- **Built-in tools**: safe arithmetic evaluator, current UTC datetime
- **Persistent memory**: key-value store (`store_memory`/`read_memory`) backed by JSON file, survives restarts
- **Notification buffer**: agent-initiated alerts via `send_notification` — only forwarded to output channel when explicitly triggered
- **Filesystem tools**: read, write, list, create directory, replace-in-file, grep
- **Shell tool**: run arbitrary bash commands (see [Security](#security))
- **Optional web search** via Tavily (enabled when `TAVILY_API_KEY` is set)
- **Message router**: clean adapter abstraction — terminal REPL, Discord bot, and periodic heartbeat run concurrently through a single router
- **Heartbeat**: periodic agent-initiated runs driven by a Markdown prompt file; agent controls forwarding via `send_notification`
- **Per-session memory**: conversations persisted per thread via `InMemorySaver`
- **Full test suite** runnable without live API keys

## Requirements

- Python >= 3.12
- [`uv`](https://github.com/astral-sh/uv) package manager

## Installation

```bash
# Install uv if needed
pip install uv

# Create virtual environment and install all dependencies
uv sync --all-groups

# Copy the environment variable template
cp .env.example .env
```

Edit `config/config.yaml` to configure the provider, model, and adapters. Copy `.env.example` to `.env` and fill in the credentials referenced by the YAML file, such as `API_KEY_LITELLM`, `DISCORD_BOT_TOKEN`, and the Matrix settings. The loader expands `${VAR}` and `${VAR:-default}` values in YAML from the environment.

## Configuration

The application always loads `config/config.yaml` on startup. Its top-level sections are:

| Section | Description |
|---|---|
| `model_provider` | Provider name, API protocol, endpoint, and API key |
| `model` | Model name and reasoning effort |
| `discord_adapter` | Discord bot token |
| `heartbeat` | Interval, prompt file, and optional output destination |
| `matrix_adapter` | Matrix homeserver, credentials, and crypto store settings |

The checked-in [`config/config.yaml`](config/config.yaml) is a complete example. Non-secret values can be literal YAML values or environment substitutions. Secret values (`api_key`, `access_token`, and `bot_token`) must use environment substitutions: `${VAR}` requires the variable to be set, while `${VAR:-}` allows an empty value for an optional integration. Non-empty secret defaults are rejected.

Optional integrations still use environment variables: `TAVILY_API_KEY` enables web search, and `LANGCHAIN_TRACING_V2`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_API_KEY`, and `LANGCHAIN_PROJECT` configure LangSmith tracing.

## Usage

### Start the agent

#### One-shot prompt mode

Run the agent once on a direct instruction without starting the long-running adapters:

```bash
uv run agent --prompt "Summarize the current git status and mention any TODOs."
```

#### Batch mode

Process one prompt per non-empty line and write structured JSONL records to disk:

```bash
cat > prompts.txt <<'EOF'
List the top-level files.
Check for TODO markers in the repo.
EOF

uv run agent --batch-input prompts.txt --batch-output results.jsonl
```

The batch output is newline-delimited JSON and includes the source line number, prompt text, event type, and content for each streamed event.

#### Example: run with a local Ollama model

```ini
# config/config.yaml
model_provider:
    name: ollama
    api: openai_compatible
    api_key: ${OLLAMA_API_KEY:-}
    endpoint: http://localhost:11434/v1
model:
    name: qwen2.5-coder:14b
    effort: low
```

> **Note:** Tool-calling reliability varies between local models. Prefer instruction-tuned/chat models with strong tool-use support.

Leave `OLLAMA_API_KEY` unset for a local Ollama server. For Ollama Cloud, set
`OLLAMA_API_KEY` in the environment and use the cloud endpoint.

```bash
uv run agent
```

This starts all enabled adapters concurrently inside a single process:

- **Terminal REPL** — type messages directly in the terminal. The agent prints `[node] response` as steps complete. Type `quit`, `exit`, `q`, or press Ctrl-D to stop.
- **Discord bot** — if `DISCORD_BOT_TOKEN` is set, the bot comes online and responds to messages in any channel it can read. A "typing…" indicator is shown while the agent works.
- **Heartbeat** — reads `HEARTBEAT.md` (configurable) and runs the agent on that prompt every N seconds. Output is written to `agent.log`. The agent controls when to forward messages to the output channel by calling `send_notification`.

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
2. Under **Bot**, create a bot user and set `DISCORD_BOT_TOKEN` in `.env`. Reference it from `discord_adapter.bot_token` in `config/config.yaml`.
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
3. Set `MATRIX_HOMESERVER_URL`, `MATRIX_ACCESS_TOKEN`, and `MATRIX_USER_ID` in `.env`, then reference them from `matrix_adapter` in `config/config.yaml`. For encrypted rooms, also set a stable `MATRIX_DEVICE_ID` and `MATRIX_STORE_PATH`.
4. The Matrix adapter is registered by the current router; ensure the credentials are present in the YAML configuration.
5. Add the bot to rooms manually using an admin account or Element. The bot does **not** auto-accept invitations.
6. Start the agent — the bot will respond to every text message in all joined rooms.

On startup, the Matrix adapter performs an initial sync before registering callbacks so it skips old backlog, then continues incremental syncs using the stored sync token when a store path is configured.

Each user × room combination gets its own persistent conversation thread (thread ID: `matrix-{room_id}-{sender_id}`). Replies are threaded using Matrix’s `m.in_reply_to` so conversations stay readable in shared rooms.

### Heartbeat

The heartbeat runs the agent on a schedule without any human input. Create a `HEARTBEAT.md` in the project root:

```markdown
Check the project status. Look at recent git commits, open TODOs, and report
anything that needs attention. Be concise. If something is worth alerting
about, call send_notification with the message.
```

Then configure the interval and, optionally, where to send the results:

```ini
# config/config.yaml
heartbeat:
    interval: 600
    prompt_file: config/HEARTBEAT.md
    output_adapter: discord
    output_channel: ${HEARTBEAT_OUTPUT_CHANNEL}
```

When `HEARTBEAT_OUTPUT_ADAPTER` and `HEARTBEAT_OUTPUT_CHANNEL` are both set, the agent can forward messages by calling `send_notification`. Normal response text is only logged — nothing reaches the output channel unless the agent explicitly calls the tool. If neither is set, output goes only to `agent.log`.

This pattern is useful for state-change detection. For example, using the memory tools to track previous state:

```markdown
Check the weather at my location.
- Read `last_weather` from memory with read_memory.
- Determine the current condition.
- If the current condition differs from memory (or memory is empty),
  call send_notification with a brief alert message.
- Always call store_memory("last_weather", "<current condition>") at the end.
- If nothing changed, do NOT call send_notification.
```

> **Note:** `HEARTBEAT_OUTPUT_ADAPTER` must match the `adapter_id` of a registered adapter (`"discord"`, `"matrix"`, `"terminal"`, or any future adapter). The output adapter must also be enabled and have valid credentials.

## Project Structure

```
src/agent/
├── __init__.py              # Public API: exports build_graph, graph
├── __main__.py              # Entry point: build_router(), main()
├── config.py                # Settings dataclasses; LLM factory
├── graph.py                 # LangGraph StateGraph assembly
├── nodes.py                 # Graph node functions (call_model)
├── state.py                 # AgentState TypedDict
│
├── tools/                   # Tool implementations
│   ├── __init__.py          # Re-exports all tool functions
│   ├── general.py           # Core tools (calculate, datetime)
│   ├── tools.py             # get_tools() aggregator + web_search
│   ├── tools_filesystem.py  # Filesystem tools (read/write/list/grep/replace)
│   ├── tools_cmd.py         # Shell tool (bash)
│   ├── tools_memory.py      # Persistent key-value memory (store_memory, read_memory)
│   ├── tools_notifications.py  # Notification buffer (send_notification)
│   └── tools_treesitter.py  # Tree-sitter parsing & query tools
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
- `HeartbeatAdapter` is agent-*initiated*: it injects `InboundMessage` objects on a schedule rather than waiting for user input. After each run it drains the notification buffer (messages queued via `send_notification`) and forwards them to the configured output adapter.

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
| `calculate` | `tools/general.py` | Safe AST-based arithmetic evaluator |
| `get_current_datetime` | `tools/general.py` | Current UTC time as ISO-8601 |
| `web_search` | `tools/tools.py` | Tavily web search (requires `TAVILY_API_KEY`) |
| `read_file` | `tools/tools_filesystem.py` | Read a file with optional line offset/count |
| `write_file` | `tools/tools_filesystem.py` | Write (or overwrite) a file |
| `list_directory` | `tools/tools_filesystem.py` | List directory contents with entry types |
| `create_directory` | `tools/tools_filesystem.py` | Create a directory (mkdir -p) |
| `replace_in_file` | `tools/tools_filesystem.py` | Replace a string inside a file |
| `grep` | `tools/tools_filesystem.py` | Regex search across files in a directory |
| `store_memory` | `tools/tools_memory.py` | Persist a key-value pair to `.agent_memory.json` |
| `read_memory` | `tools/tools_memory.py` | Retrieve a previously stored value by key |
| `send_notification` | `tools/tools_notifications.py` | Queue a message for the configured output channel (heartbeat only) |
| `bash` | `tools/tools_cmd.py` | Run an arbitrary shell command |
| `treesitter_parse` | `tools/tools_treesitter.py` | Parse a source file or code string into a JSON syntax tree (bounded by depth and character limit) |
| `treesitter_query` | `tools/tools_treesitter.py` | Run a tree-sitter S-expression query against a source file or code string; returns matched captures as JSON |
| `treesitter_get_symbols` | `tools/tools_treesitter.py` | Extract top-level symbols (functions, classes, imports) from a source file using built-in per-language queries |
| `lsp_definition` | `tools/tools_lsp.py` | Jump to the definition of a symbol at a path-anchored cursor position |
| `lsp_references` | `tools/tools_lsp.py` | Find references to a symbol at a path-anchored cursor position |
| `lsp_document_symbols` | `tools/tools_lsp.py` | List symbols defined in a single source file |
| `lsp_workspace_symbols` | `tools/tools_lsp.py` | Search workspace symbols using a path anchor to choose the backend and project root |
| `lsp_rename` | `tools/tools_lsp.py` | Return a JSON `WorkspaceEdit` for a rename without applying it |
| `lsp_diagnostics` | `tools/tools_lsp.py` | Return diagnostics for a single source file |

All filesystem and shell tools restrict access to paths inside the project working directory.

### Generic LSP tools

The six `lsp_*` tools speak the Language Server Protocol over stdio to a backend selected from the target path. C/C++ paths route to `clangd`; Python paths route to `pyright-langserver`.

**Prerequisites:**

1. **clangd** must be installed and on `PATH` (or pointed to by `CLANGD_PATH`) for C/C++ files.
2. **pyright-langserver** must be installed and on `PATH` (or pointed to by `PYRIGHT_LANGSERVER_PATH`) for Python files.
3. For best C/C++ results, provide a `compile_commands.json` in the project root. Without it, clangd falls back to best-effort parsing and results may be incomplete.

**Behaviour:**

- Clients are started lazily per backend and workspace root, then reused until the agent exits.
- The generic LSP surface intentionally excludes completion, call hierarchy, and type hierarchy; the supported tool set is limited to definition, references, symbols, rename planning, and diagnostics.
- `lsp_workspace_symbols` requires a `path` anchor so the agent can choose the correct backend and project root.
- Python requests prefer a project-local interpreter from `.venv`, `venv`, or `env`, then fall back to the running interpreter.
- Input positions use **1-based line numbers** and **0-based character offsets** (matching `treesitter_get_symbols` and editor grep conventions). Outputs convert LSP's 0-based positions to 1-based.
- All tool outputs are JSON strings capped at 8 000 characters. If a result exceeds this limit, a `{"truncated": true, "omitted_count": N}` sentinel is appended.
- The same `_is_subpath` policy gating all filesystem tools also applies here — paths outside the project root are rejected.

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

Define a new tool using the `@tool` decorator in the appropriate module, then include it in the list returned by `get_tools()` in `tools/tools.py`:

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
## Lessons learned

### Matrix

Perform an initial login using username/password to obtain a stable `access_token` and `device_id`. Persist both values and reuse them on subsequent runs instead of re-authenticating.

After the first successful login, initialize the client using the stored credentials:

- `access_token`
- `device_id`
- persistent `store_path` (e.g. `./nio_store`)

Do not call `login()` on every startup; reuse the saved session to ensure E2EE continuity.

See `example/matrix.py` for a reference implementation.
