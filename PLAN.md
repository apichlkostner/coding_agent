# Design

## Context
The current entry point in [src/agent/__main__.py](src/agent/__main__.py) starts a `MessageRouter` using environment-driven adapter registration and has no command-line parsing. Interactive terminal use is implemented by [src/agent/adapters/terminal_adapter.py](src/agent/adapters/terminal_adapter.py), which sends one prompt at a time on the fixed thread `terminal-cli` and waits for completion before reading the next input. Non-interactive agent-driven execution already exists in [src/agent/adapters/heartbeat_adapter.py](src/agent/adapters/heartbeat_adapter.py), which shows the established pattern for synthesizing `InboundMessage` objects, dispatching them through the router, awaiting completion, and formatting outbound events.

The requested change adds two one-shot CLI workflows:
1. A direct command-line prompt mode so the user can pass the instruction as an argument instead of entering the interactive REPL.
2. A batch mode so the agent can process multiple prompts in one run.

The existing router and agent service already support this behavior. `MessageRouter.dispatch()` accepts any `InboundMessage`, serializes work per `thread_id`, and streams `tool_call`, `tool_result`, `response`, and `error` messages back through the originating adapter. The main design work is therefore at the CLI and adapter boundary rather than in the graph or router core.

## Decisions
Direct prompt mode will be exposed as a dedicated CLI flag on the main entry point rather than as an environment variable. Rationale: this is transient invocation-specific behavior and should not live in persistent runtime settings.

Batch mode will accept a plain text input file containing one prompt per line. Rationale: this is the simplest starting contract, easy to create with shell tools, and avoids premature schema design.

Batch mode output will be structured and preserve intermediate events. Rationale: the router already produces meaningful streamed event types, and retaining them makes failures and tool activity observable instead of collapsing everything to a final string.

One-shot CLI modes will be exclusive and will disable the normal long-running adapters for that invocation. Rationale: `--prompt` and batch mode are expected to run to completion and exit, and mixing them with the REPL, Discord, Matrix, or heartbeat startup would create ambiguous lifecycle behavior.

The implementation will preserve the current router-based execution path instead of invoking the graph directly. Rationale: using the router keeps behavior aligned with the rest of the application, preserves existing message formatting conventions, and reuses thread serialization and outbound message handling.

## Assumptions
The direct prompt mode should behave like a single REPL submission: one input, one router dispatch, print streamed output, then exit.

Batch mode should process lines sequentially in the first version. This keeps ordering deterministic and avoids introducing concurrency controls or cross-item thread semantics before they are needed.

Blank lines in batch input can be ignored rather than treated as empty prompts.

The structured batch output can be newline-delimited JSON, with one record per outbound event and enough fields to correlate events back to the source line.

CLI flags for one-shot modes should take precedence over `ENABLED_ADAPTERS` without mutating environment-derived `Settings`.

## Approach
Add explicit CLI parsing in [src/agent/__main__.py](src/agent/__main__.py) so the entry point can choose between three invocation paths: default adapter startup, direct prompt execution, and batch execution. The default path will continue to use `get_settings()` and `build_router(settings)` unchanged for long-running adapters. The one-shot paths will still load normal model and provider settings, but they will construct a router configured only with the adapter needed for that invocation.

Introduce one or more non-interactive adapters dedicated to one-shot execution. The direct prompt path can use a small adapter that injects a single `InboundMessage`, prints outbound events using the same formatting conventions as the terminal adapter, then returns. The batch path can use a dedicated adapter that reads the input file, dispatches each non-empty line with a deterministic per-line identifier, captures every outbound event, writes structured records to an output sink, and exits after the final item completes.

Keep one-shot execution on the router path. Each batch item will be represented as its own `InboundMessage`, with a thread ID strategy chosen to isolate prompts by default while still making records traceable, for example by including the line number in the thread ID. This avoids accidental history bleed between unrelated batch items.

Extend tests around [tests/test_main.py](tests/test_main.py) and [tests/test_adapters.py](tests/test_adapters.py) to cover CLI mode selection, one-shot adapter registration, single-prompt execution, and batch input/output behavior. Reuse the project’s current testing style of mocked graphs, captured `InboundMessage` objects, and collector adapters rather than introducing end-to-end shell-based tests for the first iteration.

---

# Plan

## Step 1 — Add Direct Prompt CLI Mode

Introduce explicit CLI parsing and a one-shot `--prompt` execution path that sends a single instruction through the existing router flow, prints streamed output using terminal-style formatting, and exits without starting the long-running adapters.

### Implementation context

- **Files to change**
	- `src/agent/__main__.py` (modify) — add argument parsing, mode selection, and a one-shot execution helper for direct prompt mode.
	- `tests/test_main.py` (modify) — cover CLI mode selection and verify direct prompt mode dispatches one `InboundMessage` and avoids normal adapter startup.
- **Relevant symbols**
	- `main() -> None` — current console-script entry point.
	- `_run() -> None` — current async startup body that needs to branch between default and one-shot flows.
	- `build_router(settings, graph=None) -> MessageRouter` — should remain the default long-running router factory.
	- `MessageRouter.dispatch(message) -> asyncio.Task[None]` — existing entry point for one-shot execution.
	- `TerminalAdapter.send(message) -> None` — existing stdout formatting behavior that direct prompt mode should reuse rather than reimplement.
- **Patterns to follow**
	- Use `get_settings()` as the single source of provider/runtime configuration.
	- Follow the existing lazy graph import and best-effort LSP cleanup flow already present in `__main__.py`.
	- Match `TerminalAdapter.start()` semantics for a single prompt: create one `InboundMessage`, await the returned task, then return.
	- Keep tests in the current style used by `_mock_graph()` and `_CollectorAdapter` in `tests/test_main.py`.
- **Dependencies / call sites**
	- `agent = "agent.__main__:main"` in `pyproject.toml` remains the sole console-script entrypoint.
	- Default interactive and network adapter startup must continue working when no one-shot CLI flag is provided.
- **Gotchas**
	- Do not mutate `Settings.enabled_adapters` or environment variables to disable other adapters; one-shot mode selection should be local to the invocation path.
	- Preserve the current default behavior for `uv run agent` with no CLI flags.
	- Ensure the one-shot path still runs the existing LSP cleanup in `finally` so CLI mode changes do not leak resources.

### Tests / verification

- Run `uv run pytest tests/test_main.py`.
- Smoke check direct prompt mode with a mocked graph or patched router path to confirm one prompt is dispatched and the process exits after completion.

## Step 2 — Implement Batch Adapter And Structured Output

Create a dedicated batch adapter that reads one prompt per non-empty line, dispatches each prompt sequentially through the router, and writes newline-delimited structured event records so tool activity, responses, and failures remain visible.

### Implementation context

- **Files to change**
	- `src/agent/adapters/batch_adapter.py` (create) — implement sequential file-driven batch execution and structured output writing.
	- `src/agent/adapters/__init__.py` (modify) — export the new batch adapter alongside the existing adapters.
	- `tests/test_adapters.py` (modify) — add unit tests for input parsing, blank-line skipping, dispatch sequencing, and structured output records.
- **Relevant symbols**
	- `BaseAdapter.start(router) -> None` — contract the batch adapter must implement.
	- `BaseAdapter.send(message) -> None` — sink for router-produced outbound events.
	- `InboundMessage` — should carry per-line content plus deterministic IDs for correlation.
	- `OutboundMessage` — source of `msg_type`, `node_name`, and content for JSONL records.
	- `HeartbeatAdapter.start()` — nearest in-repo pattern for agent-initiated dispatch followed by awaiting task completion.
- **Patterns to follow**
	- Dispatch one message at a time and `await` the returned task before moving to the next line, mirroring `HeartbeatAdapter` and `TerminalAdapter` sequencing.
	- Ignore empty or whitespace-only lines, consistent with how `TerminalAdapter.start()` skips blank user input.
	- Write UTF-8 output incrementally so a partial batch run still leaves usable records behind.
	- Reuse `OutboundMessage.metadata` fields rather than inventing a parallel event taxonomy.
- **Dependencies / call sites**
	- The batch adapter will be instantiated only by the new CLI batch path in `__main__.py`.
	- Tests should use temp files and the existing mock-router pattern in `tests/test_adapters.py` rather than requiring a live graph.
- **Gotchas**
	- Each output record needs enough context to correlate back to the source line, such as line number, prompt text or prompt index, thread ID, event type, and content.
	- Thread IDs should isolate items by default to avoid conversation bleed between unrelated lines.
	- If the router surfaces an escaped exception rather than an outbound `error` event, the adapter still needs to record a structured failure for that input item.

### Tests / verification

- Run `uv run pytest tests/test_adapters.py -k batch`.
- Add a temp-file test that verifies non-empty lines produce sequential dispatches and JSONL output records with stable correlation fields.
- Add a failure-path test confirming the output stream captures error events without losing item context.

## Step 3 — Wire Batch CLI Path And Document Usage

Integrate the batch adapter into the CLI entry point, enforce one-shot mode exclusivity, and document how to invoke both direct prompt and batch execution from the command line.

### Implementation context

- **Files to change**
	- `src/agent/__main__.py` (modify) — add batch-specific CLI arguments, instantiate the batch adapter for one-shot runs, and reject incompatible flag combinations.
	- `tests/test_main.py` (modify) — cover batch mode selection, exclusive-mode validation, and the fact that long-running adapters are not started when one-shot flags are present.
	- `README.md` (modify) — add usage examples for direct prompt and batch mode, including the line-oriented batch input contract and structured output artifact.
- **Relevant symbols**
	- `main() -> None` and `_run() -> None` — central mode-selection logic.
	- `MessageRouter.register(adapter)` — used to construct one-shot routers containing only the needed adapter.
	- `MessageRouter.run() -> None` — still the right lifecycle entry point for adapter-driven batch execution.
	- `Settings.enabled_adapters` — should continue to control only the default long-running mode.
- **Patterns to follow**
	- Keep `build_router()` focused on environment-configured long-running adapters; avoid overloading it with CLI-only branching.
	- Match the repo’s documentation style in `README.md`: short usage-oriented sections with concrete shell examples.
	- Preserve the current warning/skip behavior for disabled or misconfigured long-running adapters outside one-shot mode.
- **Dependencies / call sites**
	- Users will invoke the feature through `uv run agent ...` or the installed `agent` console-script.
	- Batch mode relies on the batch adapter from Step 2 and direct prompt mode from Step 1.
- **Gotchas**
	- The CLI needs clear exclusivity rules so `--prompt` cannot be combined with batch input/output flags.
	- Batch mode needs a defined output path contract; the CLI should fail early if required batch arguments are incomplete.
	- Documentation should make it obvious that one prompt equals one line and that blank lines are ignored.

### Tests / verification

- Run `uv run pytest tests/test_main.py tests/test_adapters.py`.
- Run a manual smoke check for `uv run agent --prompt "..."`.
- Run a manual smoke check for `uv run agent --batch-input prompts.txt --batch-output results.jsonl` using a small sample file.

## Risks

- **Output schema drift** — If the batch JSONL shape is underspecified, later tooling will break. The first implementation should settle on stable field names and test them directly.
- **Mode-selection regressions** — CLI branching in `__main__.py` can accidentally change default startup behavior. Guard this with explicit tests for the no-flag path.
- **Error-path ambiguity** — The router normally emits `error` messages, but escaped exceptions can still occur. The batch adapter needs a clear fallback record shape so failures are visible and correlated.
- **Shared formatting duplication** — Direct prompt mode should avoid creating a second terminal-printing implementation if `TerminalAdapter.send()` can be reused cleanly.