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
├── __main__.py              # thin wiring: build router, register adapters, run
├── config.py                # extended: heartbeat config, adapter toggles
├── graph.py                 # unchanged
├── nodes.py                 # unchanged
├── state.py                 # unchanged
├── tools.py                 # unchanged
├── tools_cmd.py             # unchanged
├── tools_filesystem.py      # unchanged
│
├── router/
│   ├── __init__.py
│   ├── messages.py          # InboundMessage, OutboundMessage dataclasses
│   ├── base_adapter.py      # BaseAdapter ABC
│   ├── agent_service.py     # AgentService (stream logic, thread ID handling)
│   └── router.py            # MessageRouter
│
└── adapters/
    ├── __init__.py
    ├── discord_adapter.py   # refactored from discord_bot.py
    ├── terminal_adapter.py  # refactored from terminal_bot.py
    └── heartbeat_adapter.py # refactored from heartbeat_bot.py
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

### Phase 2 — Refactor existing adapters

5. `adapters/terminal_adapter.py` — wraps existing REPL; calls `router.dispatch()` per user input; `send()` prints to stdout.
6. `adapters/discord_adapter.py` — wraps `discord.Client`; `on_message` builds `InboundMessage` and calls `router.dispatch()`; `send()` chunks and posts to channel.
7. `adapters/heartbeat_adapter.py` — periodic trigger; reads `HEARTBEAT.md`; builds `InboundMessage`; configurable output destination(s) read from `config.py`.

### Phase 3 — Wire up and clean up

8. Rewrite `__main__.py`:
   ```python
   async def main():
       settings = get_settings()
       agent_service = AgentService(graph)
       router = MessageRouter(agent_service)
       router.register(TerminalAdapter())
       router.register(DiscordAdapter(token=settings.discord_token))
       router.register(HeartbeatAdapter(settings.heartbeat))
       await router.run()
   ```
9. Delete `discord_bot.py`, `terminal_bot.py`, `heartbeat_bot.py`.
10. Extend `config.py` / `.env` with heartbeat output destinations and per-adapter enable flags.

### Phase 4 — Tests

11. Unit-test `AgentService` with a mocked graph.
12. Unit-test `MessageRouter.dispatch()` with a stub adapter and mock `AgentService`.
13. Integration test: build the full router with `InMemorySaver` graph and a `TerminalAdapter` stub.

### Phase 5 — Future adapters (out of scope for now, design is ready)

- `adapters/web_adapter.py` — `aiohttp` WebSocket or Server-Sent Events endpoint; no new router changes needed.
- `adapters/telegram_adapter.py` — `python-telegram-bot` async client.
- `adapters/webhook_adapter.py` — generic inbound HTTP webhook.

---

## Configuration Extensions

Add to `.env` / `Settings`:

```ini
# Which adapters to enable (comma-separated)
ENABLED_ADAPTERS=terminal,discord,heartbeat

# Heartbeat
HEARTBEAT_INTERVAL_SECONDS=600
HEARTBEAT_OUTPUT_ADAPTERS=discord        # comma-sep list of adapter_ids
HEARTBEAT_OUTPUT_CHANNEL=<discord_channel_id>  # adapter-specific channel
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `asyncio.create_task` + per-thread `Lock` instead of a queue | Same fire-and-forget semantics as a queue; per-thread lock serialises writes to the LangGraph checkpointer without a dedicated consumer coroutine |
| `dispatch()` returns `asyncio.Task[None]` | Adapters ignore the return value; tests can `await` it to synchronise without any test-only hooks |
| Thread ID owned by adapter | Each adapter knows its own identity scheme; router/service are ID-agnostic |
| `AgentService` yields `OutboundMessage` | Keeps formatting concerns in adapters; service only classifies output type via `msg_type` |
| `verbose` flag on `AgentService` | Cheaper than adapter-side filtering for channels that never want intermediate tool steps |
| Guard for missing `"messages"` key | Future LangGraph nodes (or `__interrupt__`) may not produce messages; skipping instead of crashing is safer |
| Heartbeat as an adapter | Uniform lifecycle (start/stop); fits the same `BaseAdapter` contract as interactive adapters |
| Old bot files deleted (not kept) | Avoids confusion from two code paths doing the same thing |

---

## Open Questions

- **Concurrency per thread**: should multiple messages on the same `thread_id` be serialised (queue per thread) or processed in parallel? Current approach is serial to preserve checkpointer consistency. Decision: serialize it
- **Verbosity control**: tool-call/result messages are useful in terminal but noisy in Discord DMs — add a per-adapter verbosity flag to `BaseAdapter`. Decision: add a per-adapter verbosity flag to `BaseAdapter`
- **Error routing**: if `AgentService.run()` raises, should the error be sent back to the originating adapter, broadcast, or only logged? Propose: send back to originating adapter only. Decision: send back to originated adapter only.
- **Multi-channel heartbeat fan-out**: heartbeat response could be routed to multiple adapters simultaneously — implement in Phase 3 if needed. Decision: First only to one adapter
