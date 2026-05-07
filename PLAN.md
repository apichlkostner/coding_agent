# Message Router — Design & Implementation Plan

## Current State

The project currently has three independent "bots", each wired directly to the LangGraph `graph` singleton:

| File | Role |
|---|---|
| `discord_bot.py` | Listens for Discord messages, streams responses back |
| `terminal_bot.py` | Interactive REPL loop over stdin/stdout |
| `heartbeat_bot.py` | Periodic agent-initiated run driven by `HEARTBEAT.md` |

All three are started as separate `asyncio` tasks in `__main__.py`. Each duplicates the stream-processing loop (tool calls → tool results → final response). There is no shared abstraction, so adding a new channel (Telegram, web interface, etc.) requires copy-pasting all of that logic.

---

## Goal

Introduce a **MessageRouter** that:

1. Provides a uniform abstraction (`BaseAdapter`) for every inbound and outbound channel.
2. Centralises the agent-invocation and stream-processing logic in a single `AgentService`.
3. Supports **agent-initiated messages** (heartbeat, notifications, alerts) sent to any registered adapter/channel.
4. Allows new adapters to be added with minimal boilerplate — no changes to the core router needed.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          __main__.py                                │
│   creates router, registers adapters, starts all with asyncio       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │     MessageRouter     │
                    │                       │
                    │  - adapter registry   │
                    │  - inbound queue      │
                    │  - dispatch loop      │
                    └──────────┬────────────┘
                               │ InboundMessage
              ┌────────────────▼────────────────┐
              │          AgentService            │
              │                                  │
              │  - wraps LangGraph graph         │
              │  - manages thread IDs            │
              │  - normalises stream output      │
              └────────────────┬─────────────────┘
                               │ OutboundMessage(s)
                    ┌──────────▼──────────┐
                    │   MessageRouter     │
                    │   routes back to    │
                    │   correct adapter   │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   DiscordAdapter      TerminalAdapter      HeartbeatAdapter
   (existing)          (existing)           (existing)

                  + future adapters:
                    TelegramAdapter, WebAdapter, …
```

---

## Core Abstractions

### 1. Normalised Message Types — `router/messages.py`

```python
@dataclass
class InboundMessage:
    adapter_id: str        # "discord" | "terminal" | "heartbeat" | …
    thread_id: str         # unique conversation key for LangGraph checkpointer
    content: str           # raw text sent to the agent
    reply_channel_id: str  # where the response should go (adapter-specific)
    user_id: str | None    # human user id; None for agent-initiated triggers
    metadata: dict         # adapter-specific extras (guild, message_id, …)

@dataclass
class OutboundMessage:
    adapter_id: str        # which adapter should deliver this
    reply_channel_id: str  # adapter-specific destination
    content: str
    metadata: dict         # e.g. {"type": "tool_call"} for visual styling
```

**Thread ID conventions** (owned by each adapter, not the router):

| Source | Thread ID pattern |
|---|---|
| Discord | `discord-{user_id}-{channel_id}` |
| Terminal | `terminal-cli` |
| Heartbeat | `heartbeat` |
| Web | `web-{session_id}` |

### 2. BaseAdapter — `router/base_adapter.py`

```python
class BaseAdapter(ABC):
    adapter_id: str

    @abstractmethod
    async def start(self, router: "MessageRouter") -> None:
        """Start listening for events and call router.dispatch() for each one."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Deliver an outbound message to the user/channel."""
```

Adapters are responsible for:
- Building `InboundMessage` objects (including `thread_id` and `reply_channel_id`).
- Formatting `OutboundMessage` content for their platform (Markdown → plain text, chunking, etc.).

### 3. AgentService — `router/agent_service.py`

Single place that owns the LangGraph interaction:

```python
class AgentService:
    def __init__(self, graph: CompiledStateGraph): ...

    async def run(self, message: InboundMessage) -> AsyncIterator[OutboundMessage]:
        """Stream agent steps; yield OutboundMessage for each meaningful output."""
```

Internally this extracts the existing `astream` + step-parsing logic that is currently duplicated in all three bots. Yields:
- One `OutboundMessage` per tool-call announcement (optional, controlled by a verbosity flag).
- One `OutboundMessage` per tool result (optional).
- One `OutboundMessage` for the final agent response.

### 4. MessageRouter — `router/router.py`

```python
class MessageRouter:
    def __init__(self, agent_service: AgentService): ...

    def register(self, adapter: BaseAdapter) -> None: ...

    async def dispatch(self, message: InboundMessage) -> None:
        """Called by adapters. Runs the agent and routes responses back."""

    async def send_to(self, message: OutboundMessage) -> None:
        """Called by AgentService or internal triggers to push a message."""

    async def run(self) -> None:
        """Start all registered adapters concurrently."""
```

`dispatch` uses an `asyncio.Queue` internally so adapters can fire-and-forget without blocking the event loop.

---

## Agent-Initiated Messages

The **heartbeat** is the first agent-initiated pattern. The router needs to support it cleanly:

- `HeartbeatAdapter` creates a synthetic `InboundMessage` with `user_id=None` on its schedule.
- The `reply_channel_id` tells the router where to route the response (e.g. a Discord channel ID for monitoring, or `"stdout"` for terminal logging).
- A configurable list of `(adapter_id, reply_channel_id)` pairs in `config.py` / `.env` allows the heartbeat output to be broadcast to multiple destinations simultaneously.

Future agent-initiated use cases follow the same pattern:
- **Notifications**: a tool produces an `OutboundMessage` → router fans out.
- **Proactive check-ins**: a scheduler (APScheduler / asyncio periodic task) injects `InboundMessage` objects.
- **Alerts**: any internal component calls `router.send_to(...)` directly.

---

## Directory Layout After Refactoring

```
src/agent/
├── __main__.py              # thin wiring: build router, register adapters, run  (Phase 3)
├── config.py                # extended: HeartbeatSettings, adapter toggles
├── graph.py                 # unchanged
├── nodes.py                 # unchanged
├── state.py                 # unchanged
├── tools.py                 # unchanged
├── tools_cmd.py             # unchanged
├── tools_filesystem.py      # unchanged
│
├── router/                  # ✅ Phase 1
│   ├── __init__.py
│   ├── messages.py          # InboundMessage, OutboundMessage dataclasses
│   ├── base_adapter.py      # BaseAdapter ABC
│   ├── agent_service.py     # AgentService (stream logic, thread ID handling)
│   └── router.py            # MessageRouter
│
└── adapters/                # ✅ Phase 2
    ├── __init__.py
    ├── discord_adapter.py   # replaces discord_bot.py
    ├── terminal_adapter.py  # replaces terminal_bot.py
    └── heartbeat_adapter.py # replaces heartbeat_bot.py
```

The old `discord_bot.py`, `terminal_bot.py`, `heartbeat_bot.py` are **deleted** after the adapters are validated.

---

## Implementation Steps

### Phase 1 — Core router (no behaviour change) ✅ DONE

1. ✅ `router/messages.py` — `InboundMessage`, `OutboundMessage`, `MessageType` literal.
2. ✅ `router/base_adapter.py` — `BaseAdapter` ABC with `start()` and `send()` abstract methods.
3. ✅ `router/agent_service.py` — `AgentService.run()` async generator; stream-processing loop extracted from `discord_bot.py`.
4. ✅ `router/router.py` — `MessageRouter` with per-thread `asyncio.Lock` serialisation, `dispatch()`, `send_to()`, `run()`.
5. ✅ `router/__init__.py` — re-exports all public symbols.
6. ✅ `tests/test_router.py` — 30 tests, all passing (0 LLM calls).

**Implementation notes discovered during Phase 1:**

- **`dispatch()` returns `asyncio.Task[None]`** (not `None`). This keeps the public API fire-and-forget for adapters while letting tests `await` the task to synchronise on completion — a clean solution that needs no test-only hooks.
- **`verbose` flag on `AgentService`** (default `True`). Controls whether `tool_call` / `tool_result` messages are yielded. Adapters can also filter by `msg_type` themselves, but the flag is a cheaper gate for adapters that never want intermediate steps.
- **Guard for nodes without `"messages"` key.** Steps from internal LangGraph nodes (e.g. `__interrupt__`, future custom nodes) may not carry a `messages` key. `AgentService` skips such steps with `continue` rather than crashing.
- **`asyncio.Lock` defaultdict is safe in Python 3.11.** `asyncio.Lock()` no longer requires a running event loop since Python 3.10, so `defaultdict(asyncio.Lock)` is fine as an instance attribute.
- **Tool-result truncation uses `…` (U+2026), not `...`.** One character instead of three, matching the intent of a preview suffix.
- **Queue-based dispatch was simplified.** The plan mentioned a queue; the implementation uses `asyncio.create_task` + per-thread locks instead. This achieves the same goals (fire-and-forget, serialised per thread, concurrent across threads) with less bookkeeping.

### Phase 2 — Refactor existing adapters ✅ DONE

7. ✅ `adapters/__init__.py` — package, re-exports all three adapters.
8. ✅ `adapters/terminal_adapter.py` — REPL adapter; `start()` awaits each dispatch task before re-prompting; `send()` formats by `msg_type` with `[node_name]` prefix.
9. ✅ `adapters/discord_adapter.py` — composition over inheritance to avoid `discord.Client.start(token)` vs `BaseAdapter.start(router)` conflict; `_handle_message` wraps `await task` inside `channel.typing()`; `send()` delivers only `response`/`error`, skips tool steps.
10. ✅ `adapters/heartbeat_adapter.py` — reads prompt file once on start; dispatches on configured interval; `send()` logs all output.
11. ✅ `config.py` extended — `HeartbeatSettings` dataclass embedded in `Settings`; reads `HEARTBEAT_INTERVAL_SECONDS` and `HEARTBEAT_PROMPT_FILE` env vars.
12. ✅ `router/agent_service.py` patched — `node_name` added to every `OutboundMessage.metadata` so adapters can reproduce the old `[node] …` prefix format.
13. ✅ `tests/test_adapters.py` — 43 tests, all passing (0 LLM/Discord API calls).

**Implementation notes discovered during Phase 2:**

- **`discord.Client.start(token)` conflicts with `BaseAdapter.start(router)`** — solved by composition: a private `_DiscordClient(discord.Client)` handles Discord events and delegates to `DiscordAdapter`; `DiscordAdapter(BaseAdapter)` owns the client and has the correct `start(router)` signature.
- **Typing indicator via `await task`** — `_handle_message` wraps `await router.dispatch(inbound); await task` inside `async with channel.typing()`. The typing context exits only after the full agent run, matching the original UX.
- **Discord verbosity** — `send()` silently drops `tool_call` and `tool_result` messages. This matches the original bot's actual behavior (the buffer was overwritten by the final response, so tool steps were only logged, never shown to the user).
- **Terminal `KeyboardInterrupt` re-raised** — `start()` catches `EOFError` and returns normally, but re-raises `KeyboardInterrupt` so `asyncio.run()` can cancel all tasks and exit cleanly. Old `sys.exit(0)` is gone.
- **Terminal awaits dispatch task** — `await task` before looping back to the prompt ensures the full response is printed before the next `You:` prompt appears. This is intentional blocking, not a bug.
- **`asyncio.sleep` patch leaks into background tasks** — when patching `asyncio.sleep` to stop the heartbeat loop, any `asyncio.sleep(0)` called inside the mock dispatch task was also intercepted and raised `CancelledError` prematurely. Fixed in tests by using a plain `noop()` coroutine inside mock tasks instead of `asyncio.sleep(0)`.
- **`MagicMock.__aenter__` receives `self`** — assigning an `async def f()` directly as `mock.__aenter__ = f` causes a call-with-self failure. Fixed by using `async def f(*_)` to absorb the implicit `self` argument.
- **`HeartbeatSettings` as a frozen dataclass field** — `field(default_factory=HeartbeatSettings)` is the correct way to embed a mutable-default nested dataclass inside a `frozen=True` parent; not `default=HeartbeatSettings()`.

### Phase 3 — Wire up and clean up ✅ DONE

14. ✅ `__main__.py` rewritten — sync `main()` entry point (required by `pyproject.toml` console-script); `build_router(settings, graph)` pure factory; `_setup_logging()` centralises logging (file + stderr); old bot imports removed.
15. ✅ Old bot files deleted — `discord_bot.py`, `terminal_bot.py`, `heartbeat_bot.py`.
16. ✅ `config.py` extended — `enabled_adapters: frozenset[str]` added to `Settings`; parsed from `ENABLED_ADAPTERS` env var; not set → all three; set to empty string → none.
17. ✅ `tests/test_main.py` — 22 tests: config, `build_router()`, and 5 integration tests hitting the real compiled graph with a mocked LLM.

**Implementation notes discovered during Phase 3:**

- **`main()` must be synchronous** — the `pyproject.toml` console-script entry point (`agent.__main__:main`) is called directly by the script runner with no `asyncio.run()` wrapper. An `async def main()` would silently return an unawaited coroutine. Split into sync `main()` → `asyncio.run(_run())`.
- **`build_router()` as a pure factory** — separating construction from `main()` makes the wiring fully testable without spawning real adapters or calling `asyncio.run()`.
- **`ENABLED_ADAPTERS=""` (empty string) means no adapters** — use `os.environ.get("ENABLED_ADAPTERS")` (returns `None` when absent) rather than `os.getenv("ENABLED_ADAPTERS", "")` (returns `""` for both absent and explicitly empty). `None` → default all-three; `""` → empty set.
- **Patch context must wrap the entire invocation** — `with patch("agent.nodes._get_llm_with_tools", ...)` only replaces the function for the duration of the `with` block. Since `router.dispatch()` creates a background task and returns immediately, `await task` must also be **inside** the `with` block or the real cached LLM will be used when the task runs after the context exits.
- **Logging setup guarded against re-entrancy** — `_setup_logging()` checks `root.handlers` before adding handlers so tests that configure their own logging (or run multiple times) don’t accumulate duplicate handlers.
- **Discord skipped gracefully, not an error** — when `"discord"` is enabled but the token is absent, a `WARNING` is logged and the adapter is simply not registered. This allows development without a Discord token while still having `ENABLED_ADAPTERS=terminal,discord,heartbeat` as the default.

### Phase 4 — Tests ✅ DONE (covered across Phases 1–3)

All originally planned test items are complete:
- Unit tests for `AgentService` with mocked graph → `tests/test_router.py`
- Unit tests for `MessageRouter.dispatch()` → `tests/test_router.py`
- Adapter unit tests → `tests/test_adapters.py`
- Integration test: real compiled graph + mocked LLM + stub adapter → `tests/test_main.py`

### Phase 5 — Future adapters (out of scope for now, design is ready)

- `adapters/web_adapter.py` — `aiohttp` WebSocket or Server-Sent Events endpoint; no new router changes needed.
- `adapters/telegram_adapter.py` — `python-telegram-bot` async client.
- `adapters/webhook_adapter.py` — generic inbound HTTP webhook.

---

## Configuration Reference

All settings are loaded from environment variables (`.env` file is supported via `python-dotenv`).

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | LLM backend: `openai` or `anthropic` |
| `MODEL_NAME` | *(provider default)* | Override the model name |
| `TEMPERATURE` | `0` | LLM sampling temperature |
| `DISCORD_BOT_TOKEN` | *(empty)* | Discord bot token; Discord adapter skipped if absent |
| `ENABLED_ADAPTERS` | `terminal,discord,heartbeat` | Comma-separated list of adapters to start; unset = all three; `""` = none |
| `HEARTBEAT_INTERVAL_SECONDS` | `600` | Seconds between heartbeat agent runs |
| `HEARTBEAT_PROMPT_FILE` | `HEARTBEAT.md` | Path to the Markdown file sent to the agent on each tick |

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `asyncio.create_task` + per-thread `Lock` instead of a queue | Same fire-and-forget semantics as a queue; per-thread lock serialises writes to the LangGraph checkpointer without a dedicated consumer coroutine |
| `dispatch()` returns `asyncio.Task[None]` | Adapters ignore the return value; tests can `await` it to synchronise without any test-only hooks |
| Thread ID owned by adapter | Each adapter knows its own identity scheme; router/service are ID-agnostic |
| `AgentService` yields `OutboundMessage` | Keeps formatting concerns in adapters; service only classifies output type via `msg_type` |
| `node_name` in `OutboundMessage.metadata` | Terminal and heartbeat adapters reproduce the old `[node] …` format; Discord ignores it |
| `verbose` flag on `AgentService` | Cheaper than adapter-side filtering for channels that never want intermediate tool steps |
| Guard for missing `"messages"` key | Future LangGraph nodes (or `__interrupt__`) may not produce messages; skipping instead of crashing is safer |
| Composition over inheritance for `DiscordAdapter` | `discord.Client.start(token)` and `BaseAdapter.start(router)` have incompatible signatures; a private `_DiscordClient` inner class handles Discord events and delegates to the adapter |
| Terminal `start()` re-raises `KeyboardInterrupt` | Lets `asyncio.run()` cancel all tasks cleanly; `sys.exit()` is gone |
| Terminal `start()` awaits task | Ensures full agent response is printed before next `You:` prompt; intentional sequential UX |
| Discord `send()` drops tool steps | Matches original bot behavior (buffer was overwritten by final response; tool steps were only logged) |
| Heartbeat as an adapter | Uniform lifecycle (start/stop); fits the same `BaseAdapter` contract as interactive adapters |
| Old bot files kept until Phase 3 | Avoid breaking `__main__.py` before Phase 3 wires everything up |

---

## Open Questions / Future Work

- **Multi-channel heartbeat fan-out**: heartbeat response could be routed to multiple adapters (e.g. log + Discord channel) — needs a fan-out mechanism in `HeartbeatAdapter` or `MessageRouter`.
- **Graceful shutdown**: when `TerminalAdapter.start()` returns, `asyncio.gather` in `router.run()` keeps waiting for Discord/heartbeat. A cancellation token or shutdown event would allow any adapter to signal the whole system to stop.
- **Per-adapter verbosity**: `AgentService` has a global `verbose` flag; a per-adapter flag (passed at registration) would let Discord be quiet while terminal is verbose.
- **Web adapter** (`adapters/web_adapter.py`): `aiohttp` WebSocket or SSE endpoint — no router changes needed.
- **Telegram adapter** (`adapters/telegram_adapter.py`): `python-telegram-bot` async client.
