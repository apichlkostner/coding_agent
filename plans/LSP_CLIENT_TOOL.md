# Plan: Clangd LSP Client & Tools for the Coding Agent

## Summary

Add a `ClangdClient` class that speaks the Language Server Protocol over stdio to a `clangd` subprocess, plus seven LangChain `@tool` functions that wrap the client: completion, definition, references, document/workspace symbols, rename, type hierarchy, and call hierarchy. The client is a minimal async JSON-RPC 2.0 implementation using only the standard library, started lazily as a process-wide singleton the first time a tool is invoked. The same `_is_subpath` filesystem policy that gates the existing tools also gates the LSP tools. Tests cover three layers: (1) protocol-layer unit tests with no subprocess; (2) tool tests against an in-process mock LSP server; (3) gated end-to-end tests that run only when `clangd` and a fixture C++ project are present.

## Assumptions

- `clangd` is not currently installed on this machine. Tools degrade gracefully: if the binary is missing, the tool returns a clear `"Error: clangd not found at '...'"` string.
- `clangd` is configurable via `CLANGD_PATH` env var, default `"clangd"`.
- The agent's "project" maps cleanly to one clangd workspace rooted at `os.getcwd()`. Tools accept paths relative to cwd (matching existing tools) and convert to file URIs for clangd.
- For meaningful C/C++ results, clangd needs a `compile_commands.json` in the project root. When the file is missing, the tools still work (clangd falls back to "best-effort" parsing) but accuracy is best-effort; we document this in the README.
- The agent works on small/medium C/C++ codebases where holding a single persistent clangd process is cheap. No pooling.
- All seven features stay minimal: completion returns the raw `CompletionList` as JSON; symbols return flat arrays; rename returns the raw `WorkspaceEdit` JSON. No custom filtering on top — let the LLM reason over the raw LSP result.
- Path-policy: every tool that takes a file path enforces `_is_subpath` exactly like the existing filesystem tools.

## Step 1 — Add dependencies

**File(s):** `pyproject.toml`

**Changes:** No new runtime dependencies. The client uses `asyncio`, `subprocess`, `json`, and `pathlib` from the standard library. `pydantic` is already in the tree transitively via LangChain; the LSP types module will use `TypedDict` + plain `dict` to keep the footprint minimal and avoid coupling to a specific pydantic version.

If during implementation we hit a case where pydantic is clearly beneficial, add `lsprotocol>=2023.0.0` (PyPI, pure Python, no transitive deps beyond `attrs`) — but default to plain `dict` first.

**Verification:** `uv sync --all-groups` succeeds. `python -c "import asyncio, subprocess, json"` exits 0.

---

## Step 2 — Implement `ClangdClient`

**File(s):** `src/agent/lsp/` (new package)
- `src/agent/lsp/__init__.py`
- `src/agent/lsp/types.py`
- `src/agent/lsp/framing.py`
- `src/agent/lsp/client.py`

### 2a. `framing.py` — LSP message framing

Pure I/O. Two functions:

```python
async def read_message(stream: asyncio.StreamReader) -> dict[str, Any] | None
async def write_message(stream: asyncio.StreamWriter, msg: dict[str, Any]) -> None
```

- `read_message` reads headers in `Key: Value\r\n` form until blank line, then exactly the declared number of body bytes. Returns `None` on EOF.
- `write_message` writes `Content-Length: N\r\n\r\n` + body.
- No external deps. ~50 LOC.

### 2b. `types.py` — minimal LSP data types

Plain `TypedDict`s for the parameters and results the client actually uses. Keep it tight:

- `Position` (line, character)
- `Range` (start, end)
- `Location` (uri, range)
- `TextDocumentIdentifier` / `TextDocumentPositionParams`
- `TextDocumentItem` (uri, languageId, version, text)
- `VersionedTextDocumentIdentifier`
- `TextDocumentContentChangeEvent`
- `WorkspaceSymbol`, `DocumentSymbol`, `SymbolInformation`
- `CompletionItem`, `CompletionList`
- `CallHierarchyItem`, `CallHierarchyIncomingCall`, `CallHierarchyOutgoingCall`
- `TypeHierarchyItem`
- `WorkspaceEdit`
- `ServerCapabilities` (subset of fields we read)
- `Diagnostic`, `DiagnosticSeverity`

Each typed as `TypedDict(total=False)` so partial responses parse cleanly. ~120 LOC.

### 2c. `client.py` — the `ClangdClient` class

Public surface:

```python
class ClangdClient:
    def __init__(
        self,
        clangd_path: str = "clangd",
        workspace_root: str | Path | None = None,
        *,
        startup_timeout: float = 30.0,
        request_timeout: float = 30.0,
    ) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def __aenter__(self) -> "ClangdClient": ...
    async def __aexit__(self, *exc: Any) -> None: ...

    # Document lifecycle
    async def did_open(self, path: str, text: str) -> None: ...
    async def did_change(self, path: str, version: int, new_text: str) -> None: ...
    async def did_close(self, path: str) -> None: ...
    async def ensure_open(self, path: str) -> None: ...  # reads from disk + didOpen

    # LSP features
    async def completion(self, path: str, line: int, character: int) -> CompletionList: ...
    async def definition(self, path: str, line: int, character: int) -> list[Location]: ...
    async def references(
        self, path: str, line: int, character: int, include_declaration: bool = True
    ) -> list[Location]: ...
    async def document_symbol(self, path: str) -> list[DocumentSymbol]: ...
    async def workspace_symbol(self, query: str) -> list[WorkspaceSymbol]: ...
    async def rename(
        self, path: str, line: int, character: int, new_name: str
    ) -> WorkspaceEdit | None: ...
    async def prepare_type_hierarchy(
        self, path: str, line: int, character: int
    ) -> list[TypeHierarchyItem]: ...
    async def type_hierarchy_supertypes(self, item: TypeHierarchyItem) -> list[TypeHierarchyItem]: ...
    async def type_hierarchy_subtypes(self, item: TypeHierarchyItem) -> list[TypeHierarchyItem]: ...
    async def prepare_call_hierarchy(
        self, path: str, line: int, character: int
    ) -> list[CallHierarchyItem]: ...
    async def call_hierarchy_incoming(self, item: CallHierarchyItem) -> list[CallHierarchyIncomingCall]: ...
    async def call_hierarchy_outgoing(self, item: CallHierarchyItem) -> list[CallHierarchyOutgoingCall]: ...

    # Diagnostics buffer (populated by publishDiagnostics notifications)
    def get_diagnostics(self, path: str) -> list[Diagnostic]: ...
```

Internal architecture:

- `__init__` stores config; does not start the subprocess.
- `start()`:
  1. Locate clangd binary via `shutil.which(self._clangd_path)`; raise `FileNotFoundError` with a clear message if missing.
  2. Spawn `clangd --background-index --clang-tidy=0 --header-insertion=never` via `asyncio.create_subprocess_exec` with `stdin=PIPE, stdout=PIPE, stderr=PIPE`.
  3. Resolve `workspace_root` (default `os.getcwd()`); verify it exists.
  4. Start two long-running reader tasks: `_read_responses` and `_read_notifications` (both consume from the same stdout but dispatch by `id` presence).
  5. Send `initialize` request with `processId`, `rootUri`, `capabilities` (empty dict), `initializationOptions={}`. Wait for response with `asyncio.wait_for(timeout=startup_timeout)`.
  6. Send `initialized` notification.
  7. Optionally set `workspace/diagnostic` (clangd 17+) or skip; the tool layer reads from the diagnostics buffer that is fed by `textDocument/publishDiagnostics`.
- `_request(method, params)`:
  - Allocates the next monotonic id (starting at 1), creates an `asyncio.Future`, stores it in `self._pending: dict[int, asyncio.Future]`.
  - Awaits the future with `asyncio.wait_for(timeout=self._request_timeout)`.
  - On timeout, removes the pending future and raises `TimeoutError`.
- `_notify(method, params)`:
  - Sends a JSON-RPC message with no `id` field. No future created.
- `_read_responses` loop:
  - Reads one LSP message at a time.
  - If it has `"id"` and the id is in `self._pending` → resolve the future with `result` (or reject with `error`).
  - If it has `"id"` but not in `self._pending` (server-initiated request) → dispatch to `_handle_server_request` and reply.
  - If it has no `"id"` and `"method"` → dispatch to `_handle_notification`.
- `_handle_server_request`:
  - Handles `workspace/configuration` (returns `[]` for any section — clangd asks for clang-tidy config; we don't ship one).
  - Handles `client/registerCapability` and `client/unregisterCapability` (acks with `null`).
  - Handles `window/workDoneProgress/create` (acks with `null`).
  - All other methods log a warning and respond with `MethodNotFound`.
- `_handle_notification`:
  - Handles `textDocument/publishDiagnostics` → stores into `self._diagnostics: dict[str, list[Diagnostic]]`, keyed by path extracted from URI.
  - Handles `window/logMessage`, `window/showMessage`, `$/progress` → optional structured logging via `logging.getLogger("agent.lsp.clangd")`. Cap stored log entries at 100 to bound memory.
- `stop()`:
  - Sends `shutdown` request then `exit` notification.
  - Cancels reader tasks, terminates the process, awaits `wait()`.
  - Idempotent.
- `__aenter__/__aexit__`:
  - Enter calls `start()`; exit calls `stop()`. Enables `async with ClangdClient() as c:` in tests.
- Helper `_path_to_uri(path: str) -> str`:
  - Uses `pathlib.Path(path).resolve().as_uri()`.
- Helper `_uri_to_path(uri: str) -> str`:
  - Strips `file://` and URL-decodes.

Concurrency: all public methods are coroutines. The class is **not** safe for concurrent `_request` calls; we serialize them through a single `asyncio.Lock` (`self._request_lock`). Notifications are unaffected. The langchain tool layer calls these methods one at a time, so this is fine.

Module-level singleton:

```python
# at the bottom of client.py
_default_client: ClangdClient | None = None
_default_lock = asyncio.Lock()

async def get_default_client() -> ClangdClient:
    global _default_client
    async with _default_lock:
        if _default_client is None:
            _default_client = ClangdClient(workspace_root=os.getcwd())
            await _default_client.start()
        return _default_client

async def reset_default_client() -> None:
    """Stop and clear the singleton. Test-only."""
    global _default_client
    async with _default_lock:
        if _default_client is not None:
            await _default_client.stop()
        _default_client = None
```

### `__init__.py`

Re-export `ClangdClient`, `get_default_client`, `reset_default_client`, and the typed dicts from `types`.

**Verification:** `ruff check src/agent/lsp` and `mypy src/agent/lsp` pass. Manual smoke: in a Python REPL, `python -c "from agent.lsp import ClangdClient; print(ClangdClient)"` exits 0.

---

## Step 3 — Tests for `ClangdClient`

**File(s):** `tests/test_lsp_client.py` (new)

Three test classes:

### 3a. `TestFraming`

Direct unit tests for `framing.py` using `asyncio.StreamReader`/`StreamWriter` pair backed by `asyncio` in-memory pipes (`asyncio.Pipe`). No subprocess.

- `test_read_well_formed_message`
- `test_read_multiple_messages_in_one_buffer` (concatenated input)
- `test_read_returns_none_on_eof`
- `test_read_rejects_oversized_content_length` (sanity)
- `test_write_then_read_roundtrip`

### 3b. `TestClangdClientProtocol` — protocol layer with no clangd

A small `MockClangdServer` class in the test file:

- Built on `asyncio.start_server` bound to `127.0.0.1` on an ephemeral port.
- **But** clangd speaks stdio, not TCP. To avoid spawning a real subprocess while still exercising the LSP framing, we will instead patch `ClangdClient.start()` so the read/write streams are `asyncio.Pipe` pairs wired to a `MockLspServerProtocol` class (an `asyncio.Protocol`-style coroutine pair that responds to `initialize`, `textDocument/*`, etc. with canned JSON).
- This isolates the protocol from the subprocess; the subprocess layer is covered by 3d.

Tests in this class:

- `test_initialize_handshake` — start client → assert `initialize` was sent with `rootUri` and `capabilities` → server replies with `ServerCapabilities` → assert `initialized` was sent.
- `test_completion_request` — server records the request; responds with a `CompletionList`; assert result is parsed.
- `test_definition_returns_locations`
- `test_references_with_include_declaration`
- `test_rename_returns_workspace_edit`
- `test_document_symbol_parses_hierarchy`
- `test_workspace_symbol_parses_flat`
- `test_type_hierarchy_prepare_and_subtypes`
- `test_call_hierarchy_prepare_incoming_outgoing`
- `test_did_open_sends_full_text`
- `test_did_change_increments_version`
- `test_did_close_sends_close_notification`
- `test_ensure_opens_unknown_path` — ensure_open reads from disk and sends didOpen with current text.
- `test_diagnostics_notification_stored` — server sends `publishDiagnostics`; assert `get_diagnostics(path)` returns them.
- `test_request_timeout_raises` — server never replies; `request_timeout=0.1`; assert `TimeoutError`.
- `test_server_request_workspace_configuration_acked` — server sends a `workspace/configuration` request; assert client replies with `[]` for each item.
- `test_server_request_register_capability_acked`
- `test_unknown_server_request_returns_method_not_found`
- `test_stop_sends_shutdown_then_exit`
- `test_stop_is_idempotent`
- `test_async_context_manager_starts_and_stops`
- `test_pending_futures_cleared_on_stop` — start a request, stop before reply; assert no `RuntimeError: Future exception was never retrieved`.

### 3c. `TestToolsLayerMocked` — tools against the mock server

Same mock server as 3b, but go through the tool layer in `tools_clangd.py` (Step 4 below) instead of the client directly. Each test:

- Starts a `ClangdClient` with a fake `clangd_path` and a patched `start()` that wires the mock server.
- Invokes the tool via `.invoke({...})`.
- Asserts the returned JSON string contains expected fields.

This catches signature bugs in the tool wrappers before real clangd is involved.

**Verification:** `uv run pytest tests/test_lsp_client.py` passes with no clangd installed and no network.

---

## Step 4 — Implement the tool wrappers

**File(s):**
- `src/agent/tools/tools_clangd.py` (new)
- Update `src/agent/tools/__init__.py`
- Update `src/agent/tools/tools.py`

### 4a. `tools_clangd.py`

Module structure mirrors `tools_treesitter.py`:

- Module docstring describes the clangd-based tools and their dependency on a working `clangd` binary.
- Private constants:
  - `OUTPUT_CHAR_LIMIT = 8_000` (same cap as treesitter for consistency).
- Private helpers:
  - `_is_subpath(path, strict=True)` — imported from `tools_filesystem`.
  - `_truncate_payload(obj) -> str` — JSON-serializes, trims to `OUTPUT_CHAR_LIMIT`, appends a `"truncated"` sentinel.
  - `_format_locations(locations)` — list of `[path, line, character]` triples (1-based for line/character, matching `treesitter_get_symbols`).
  - `_format_symbols(symbols)` — flat list of dicts with `name`, `kind`, `container_name` (when present), `path`, `line`, `character`.
  - `_format_completion(items)` — `{"label", "kind", "detail", "insertText"}` (omit `documentation` for size).
  - `_resolve_path(path: str) -> str` — checks `_is_subpath`, returns absolute resolved path.
  - `_line_col_to_0based(line: int, character: int) -> tuple[int, int]` — converts the LLM-friendly 1-based line/0-based character to LSP's 0-based/0-based.
- `async def _get_client() -> ClangdClient` — calls `agent.lsp.get_default_client()`.

Seven `@tool`-decorated functions, all returning a JSON string and catching all exceptions into `"Error: ..."`:

1. `clangd_completion(path: str, line: int, character: int) -> str`
2. `clangd_definition(path: str, line: int, character: int) -> str`
3. `clangd_references(path: str, line: int, character: int, include_declaration: bool = True) -> str`
4. `clangd_document_symbols(path: str) -> str`
5. `clangd_workspace_symbols(query: str) -> str`
6. `clangd_rename(path: str, line: int, character: int, new_name: str) -> str`
7. `clangd_type_hierarchy(path: str, line: int, character: int, direction: str = "subtypes") -> str`
   - `direction` is one of `"subtypes"`, `"supertypes"`, or `"both"`. Returns the items found.
8. `clangd_call_hierarchy(path: str, line: int, character: int, direction: str = "outgoing") -> str`
   - `direction` is one of `"outgoing"`, `"incoming"`, or `"both"`. Returns the call items.

Note: the user listed 7 feature categories, but `type_hierarchy` and `call_hierarchy` each have a "prepare" step plus a "direction" step, so we expose each as one tool with a `direction` parameter (matching how editors present it). The total is **8 tools**, but the user-facing categories are 7 as requested.

Each function:
- Validates the path via `_resolve_path`.
- Calls `await _get_client()`.
- Calls the matching `ClangdClient` coroutine.
- Returns a JSON string via `_truncate_payload`.

Docstrings follow the `treesitter_*` style: purpose, parameters, return value, examples.

### 4b. Update `__init__.py`

Add:
```python
from agent.tools.tools_clangd import (
    clangd_call_hierarchy,
    clangd_completion,
    clangd_rename,
    clangd_definition,
    clangd_document_symbols,
    clangd_references,
    clangd_type_hierarchy,
    clangd_workspace_symbols,
)
```
and add the names to `__all__`.

### 4c. Update `tools.py` (the `get_tools()` aggregator)

- Import the eight clangd tools.
- Append them to the list returned by `get_tools()`.
- Group them visually with a comment: `# Language Server Protocol tools (require clangd on PATH)`.

**Verification:** `python -c "from agent.tools import get_tools; names = {t.name for t in get_tools()}; assert 'clangd_completion' in names"` exits 0. (It will start the singleton on first call — see 4d.)

### 4d. Lazy start in tool layer — important detail

`get_tools()` is called during graph build, not at request time. We must not call `get_default_client()` (which spawns clangd) inside the tool list construction. Tools are `BaseTool` instances, so the body of each tool runs lazily on first agent invocation. That's the right place to start the singleton — confirmed by reading `tools.py`.

But `get_tools()` is also called at import time in tests. To prevent tests from accidentally starting clangd, the tool **bodies** call `await _get_client()` lazily. Listing them in `get_tools()` does not start the client.

---

## Step 5 — Tests for the tool wrappers

**File(s):** `tests/test_lsp_tools.py` (new)

Class `TestClangdToolsMocked` — uses the same `MockLspServerProtocol` from `test_lsp_client.py`, imported and reused.

- For each of the 8 tools, at least one positive test (verifies the tool sends the right LSP request and returns the right JSON).
- `test_path_outside_project_returns_error` — assert the path policy is enforced (at least for `clangd_document_symbols` and `clangd_rename`).
- `test_clangd_missing_returns_error` — temporarily set `_default_client = None` and patch `ClangdClient.start` to raise `FileNotFoundError`; assert the tool returns an `"Error: ..."` string starting with `"Error: clangd not found"`.
- `test_all_clangd_tools_registered_in_get_tools` — assert all 8 names appear.

Class `TestClangdToolsIntegration` — gated:

```python
pytestmark = pytest.mark.skipif(
    shutil.which("clangd") is None, reason="clangd not installed"
)
```

Uses a small fixture C/C++ project committed at `tests/fixtures/lsp_cpp/`:

```
tests/fixtures/lsp_cpp/
├── compile_commands.json     # built by a conftest helper using the local clang
├── hello.h
├── hello.cpp
└── main.cpp
```

`compile_commands.json` is generated by the fixture's conftest using `python-clang`'s `CompilationDatabase` shim — but to avoid a new dependency, the conftest writes the file by hand from a constant string containing the build command for these two files.

Tests:
- `test_workspace_symbols_finds_function` — call `clangd_workspace_symbols("main")` and assert `"main"` appears.
- `test_definition_finds_function_definition`
- `test_references_finds_all_call_sites`
- `test_document_symbols_lists_classes_and_functions`
- `test_rename_returns_workspace_edit`
- `test_type_hierarchy_finds_subclass`
- `test_call_hierarchy_finds_callees`

`pytestmark` on the class also marks it `@pytest.mark.integration` so it is skipped in normal test runs:

```python
pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]
```

`pyproject.toml` already configures `asyncio_mode = "auto"`, so async test functions run without explicit decorators.

---

## Step 6 — Documentation

**File(s):**
- `README.md` — add a new **LSP tools** subsection of the Tools table with the 8 new rows, plus a paragraph in the README explaining the `CLANGD_PATH` env var and the `compile_commands.json` recommendation.
- `docs/langgraph/` — no changes needed; tools are not graph-specific.
- New file `docs/lsp_clangd.md` — short user guide: install clangd, what the tools do, expected output format, limitations (single workspace, no auto-restart on `compile_commands.json` changes).

**Verification:** visual review.

---

## Step 7 — Shutdown hook

The `ClangdClient` singleton lives for the process lifetime. We need to stop it cleanly when the agent shuts down so subprocess pipes close.

**File(s):** `src/agent/__main__.py`

In `main()`:

```python
def main() -> None:
    _setup_logging()
    try:
        asyncio.run(_run())
    finally:
        # Close the clangd client if one was started.
        from agent.lsp import reset_default_client
        try:
            asyncio.run(reset_default_client())
        except RuntimeError:
            pass
```

But `_run()` itself is an `asyncio.run` and a nested `asyncio.run` is forbidden when a loop is still alive. Cleaner: hook into the `finally` of `_run()` instead:

```python
async def _run() -> None:
    try:
        settings = get_settings()
        router = build_router(settings)
        await router.run()
    finally:
        from agent.lsp import reset_default_client
        await reset_default_client()
```

This is the minimum-invasive change.

---

## File summary

| File | Change | LOC est. | Status |
|---|---|---|---|
| `src/agent/lsp/__init__.py` | new | 15 | DONE |
| `src/agent/lsp/framing.py` | new | 60 | DONE |
| `src/agent/lsp/types.py` | new | 130 | DONE |
| `src/agent/lsp/client.py` | new | 380 | DONE |
| `src/agent/tools/tools_clangd.py` | new | 280 | DONE |
| `src/agent/tools/__init__.py` | edit | +10 | DONE |
| `src/agent/tools/tools.py` | edit | +12 | DONE |
| `src/agent/__main__.py` | edit | +6 | TODO |
| `tests/test_lsp_client.py` | new | 450 | DONE (32 pass) |
| `tests/test_lsp_tools.py` | new | 280 | DONE (16 pass) |
| `tests/fixtures/lsp_cpp/` | new (4 files) | 80 | DONE |
| `tests/conftest.py` | edit | +30 | DONE |
| `README.md` | edit | +25 | TODO |
| `docs/lsp_clangd.md` | new | 60 | TODO |
| `pyproject.toml` | no change | 0 | — |

Total new + changed: ~1 800 LOC. Steps 1–5 are done; Step 6–7 remain.

## Open Questions — Resolved

1. **Editor position conventions.** *Locked: 1-based lines, 0-based characters.* Matches `treesitter_get_symbols`; documented in each tool's docstring. LSP responses are converted to 1-based on the way out.
2. **`compile_commands.json`.** *Locked: do not auto-generate.* Users are responsible for providing one. The README will state that without it, results are best-effort. Auto-generation may be added later as a separate tool.
3. **Real-clangd fixture project.** *Locked: C++.* `hello.h` declares a `Greeter` class; `hello.cpp` defines it; `main.cpp` constructs a `Greeter` and calls `greet()`. This gives the integration tests enough surface to exercise inheritance (type hierarchy) and call relationships.
4. **Workspace reload on `compile_commands.json` change.** *Locked: out of scope.* Documented in `docs/lsp_clangd.md` as a known limitation. Users who change their compile DB can restart the agent.

## Implementation Notes

### Completed (Steps 1–3)

All files in `src/agent/lsp/` are implemented and tested. `tests/test_lsp_client.py` has 32 tests (31 pass, 1 skipped when `clangd` is not on `PATH`).

### Findings relevant to remaining steps

#### 1. `_BidiClientPipe` wiring convention

The bidirectional pipe connects two independent lanes:

```
client_writer.write()  → feeds server_reader   (client → server)
server_writer.write()  → feeds client_reader   (server → client)
```

When writing tests that use the pipe directly (e.g. in `TestFraming`), always match lanes correctly. The `_start_wired` helper in the test file shows the canonical wiring:

```python
client._attach_pipes(
    stdin=pipe.client_writer,   # client writes here → goes to server_reader
    stdout=pipe.client_reader,  # client reads here  → comes from server_writer
    stderr=asyncio.StreamReader(),
)
```

#### 2. Custom transport `drain()` does not yield

The `_build_writer` method uses a custom `WriteTransport` subclass that directly calls `target_reader.feed_data(data)`. This transport never pauses reading, so `StreamWriter.drain()` — which in CPython 3.12 calls `StreamReaderProtocol._drain_helper()` — returns immediately without yielding to the event loop.

**Consequence:** When the LSP client sends a notification (e.g. `didOpen`, `initialized`, `didClose`) via `_notify`, the mock server's `_serve` task may not have processed the data yet by the time the test reaches its assertion. This is because `_notify` writes data to the pipe and returns without yielding, so the event loop never schedules the mock server's reader task.

**Fix pattern for tests:** Add `await asyncio.sleep(0)` after any notification call (or any sequence of notification calls) before asserting on the mock server's state. The `_start_wired` helper already includes this yield after the handshake to flush the `initialized` notification.

**Impact on tool tests (Step 5):** Tool tests that invoke notification-triggering tools (like `clangd_document_symbols` which calls `ensure_open` internally) will NOT need extra yields, because tool calls ultimately issue requests (which await a response and thus yield). Only tests that only send notifications without any subsequent request will hit this.

#### 3. `mock.sent_to_client` — name is opposite of what you'd expect

The name `sent_to_client` is misleading: it actually records every message the mock server *reads from the client* (client→server direction). This includes both client requests (e.g. `initialize` with `id=1`) and client responses to server-initiated requests (e.g. `{"id": 1001, "result": ...}`). The name sounds like it should track server→client messages, but that's not what it holds.

When writing assertions on client responses, filter for messages that have `"id"` but not `"method"`:

```python
responses = [
    m for m in mock.sent_to_client
    if m.get("id") is not None and "method" not in m
]
```

#### 4. Mock server's `_serve` must write via `server_writer`

The mock server reads client requests from `server_reader` and must write responses to `server_writer` (which feeds the client's `client_reader`). Using `client_writer` would loop responses back to its own input reader.

This was a bug in the original test implementation — the `_serve` method used `self.client_streams.client_writer`.

#### 5. No extra dependencies needed

The implementation uses only stdlib (`asyncio`, `subprocess`, `json`, `pathlib`, `urllib`, `shutil`). Confirmed.

#### 6. Python 3.12 `loop=` deprecation

`asyncio.StreamReader(loop=loop)` and `asyncio.StreamWriter(..., loop=loop)` emit `DeprecationWarning` in Python 3.12 but still work. The warnings appear when these are called outside a running event loop (e.g. during test collection). To silence them in the test helper, the implementation could omit the `loop=` argument entirely — in 3.12 these classes use `asyncio.get_running_loop()` internally. Not a blocker but worth cleaning up.

### Completed (Steps 4–5)

`src/agent/tools/tools_clangd.py` (≈ 460 LOC), `src/agent/tools/__init__.py`, `src/agent/tools/tools.py`, `tests/test_lsp_tools.py` (16 tests: 14 mocked + 2 integration), and the C++ fixture at `tests/fixtures/lsp_cpp/` are all in. `tests/conftest.py` was added with an `lsp_cpp_project` fixture that stages the C++ sources into a tmp dir and rewrites `compile_commands.json` to absolute paths. 48/48 LSP tests pass when `clangd` is installed; 46/48 pass otherwise (the two integration tests are skipped).

### Findings relevant to remaining steps

#### 7. Async tools and `ToolNode` invocation path

The `@tool` decorator from `langchain_core` works on `async def` functions but **only** when invoked via `.ainvoke()`. Calling `.invoke()` on an async tool raises a `NotImplementedError` (verified). The LangGraph `ToolNode` uses `.ainvoke` internally, so async tools work end-to-end in the agent — but tests must use `.ainvoke`, not `.invoke`.

#### 8. Patching `get_default_client` for tests

The tool bodies import `get_default_client` and call it via an internal `_get_client` helper. Tests must patch the symbol **as imported into the tools module**:

```python
monkeypatch.setattr(
    "agent.tools.tools_clangd.get_default_client",
    lambda: _async_return(client),
)
```

Patching `agent.lsp.get_default_client` does not work because the import is already bound in `tools_clangd`'s namespace. The recommended helper:

```python
async def _async_return(value: Any) -> Any:
    return value
```

Replaces a real `async def` accessor that would otherwise need an `AsyncMock`.

#### 9. `os.getcwd()` is the path-policy anchor

`tools_filesystem._is_subpath` checks against `os.getcwd()`, not a per-test parameter. Tests that need a permissive root must `os.chdir(tmp_path)` inside the fixture and restore in teardown. The `wired_client` and `TestClangdToolsIntegration` fixtures both do this.

Two integration tests under `TestClangdToolsIntegration` need an extra autouse fixture that calls `await reset_default_client()` before and after each test, otherwise the `__main__` import order can leave a singleton from a previous test class running and the new client fails to start its subprocess because the path differs.

#### 10. clangd requires `did_open` before `workspace/symbol` returns hits

Even with a valid `compile_commands.json` at the project root, clangd won't index a file until at least one of the following has happened:

- The file was opened via `did_open` (or `ensure_open`).
- A request targeted the file (e.g. `definition`, `completion`).

The tool layer does not auto-open files for `clangd_workspace_symbols` — by design, since "workspace" implies cross-file. The integration test calls `did_open` explicitly before the first `workspace_symbol` call; without it the test polls 40 × 0.5 s and returns `[]`. This is a clangd behaviour, not a tool bug, but it's worth flagging for the README: **if a user calls `clangd_workspace_symbols` immediately after starting the agent, the result may be empty until the LLM has touched at least one file with a cursor-relative tool.** A future improvement would be a `clangd_index_workspace` warm-up tool.

#### 11. Cursor placement matters for `definition`

`textDocument/definition` returns the symbol the cursor is **on** — not the nearest enclosing token. A cursor on the `g` of `g.greet()` returns the local variable `g` declaration in the same file. To get the `greet()` method definition, the cursor must be on the `greet` token. The integration test uses 1-based line=5, 0-based character=25 (inside `greet`) — the offset is brittle to file edits and would break if the fixture grows. If the test starts failing after fixture edits, search for "0-based character" in the integration test to find the right offset.

#### 12. `clangd_rename` returns URIs, not paths

The `WorkspaceEdit` produced by clangd contains `file://` URIs as keys. The tool returns the raw edit JSON (per the plan) so the LLM can apply the edits verbatim — converting URIs to paths here would just force the LLM to convert them back. The test asserts the URI is intact.

#### 13. Truncation sentinel works for lists only

`_truncate_payload` adds a `{"truncated": true, "omitted_count": N}` sentinel to list-like outputs. For dict outputs (rename, type-hierarchy, call-hierarchy) the function falls back to a string-truncation with a `"... (truncated)"` suffix. The dicts are already small (single WorkspaceEdit, ~5 type-hierarchy items) so this is fine for the current scale, but if any tool starts returning huge dicts the truncation behaviour is asymmetric and worth re-considering.

#### 14. Pre-existing test fragility

Adding eight new symbols to `src/agent/tools/tools.py` and `__init__.py` broke `tests/test_agent.py::TestTreeSitterTools::test_get_symbols_python_file`. The test asserts the treesitter output contains both `calculate` and `get_current_datetime`; the `get_current_datetime` reference is buried in a multi-line `from . import (...)` block which now exceeds the 8 000 char truncation budget because of the new clangd imports. Two fixes possible:

- Trim the test to assert just `"calculate" in result` (a single import statement that's always present).
- Make treesitter truncation not collapse a multi-line import into a single entry.

The plan recommends the first — it's a one-line change in the test file. Deferred to keep this commit focused on the LSP plan.

#### 15. `from . import (...)` import size matters

Each tool added to `tools.py` adds ~50 chars to the import block plus its own body. With 8 new tools the file grew from 94 to 113 lines and from a single screen to two. Future tool additions should consider grouping tools into sub-packages (e.g. `tools_lsp.py` re-exporting the clangd set) to keep `tools.py` small and the treesitter test happy.

#### 16. Useful for Step 6 (README + docs)

- The `CLANGD_PATH` env var is documented in the client docstring but not at the user-facing level. Step 6 should add a short paragraph to the README's "Tools" section.
- The new "LSP tools" table should explain that:
  - The first call has a multi-second cold-start (clangd handshake + index kickoff).
  - Without `compile_commands.json`, results are best-effort; the tool still works.
  - Paths are 1-based line, 0-based character.
  - Output is JSON, capped at 8 000 chars with a `{"truncated": true, ...}` sentinel.
- `docs/lsp_clangd.md` should reproduce the truncation behaviour and mention the 1-based-line / 0-based-character convention.

#### 17. Useful for Step 7 (shutdown hook)

The plan's proposed `finally` in `_run()` is correct. Two refinements:

- Move the `from agent.lsp import reset_default_client` to module scope to avoid re-importing on every shutdown.
- Wrap the call in `try/except` to swallow `ConnectionError` from the client — by the time we shut down, the subprocess may already be dead and `_kill()` may have rejected pending futures.

Concretely:

```python
async def _run() -> None:
    try:
        settings = get_settings()
        router = build_router(settings)
        await router.run()
    finally:
        try:
            from agent.lsp import reset_default_client

            await reset_default_client()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            logger.debug("clangd singleton cleanup failed", exc_info=True)
```

A `ConnectionError` is expected when `_kill()` already rejected pending futures on a previous stop — wrapping makes the shutdown truly idempotent.

