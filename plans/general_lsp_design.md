# Generic LSP Tooling For Clangd And Pyright

## Summary

Replace the current clangd-specific tool surface with a smaller generic LSP tool family that auto-selects a language server from the target path. Keep the existing JSON-returning, non-mutating pattern for rename. Support C/C++ through clangd and Python through pyright-langserver. Add first-class diagnostics. Keep real-server integration tests for both backends.

## Agreed Decisions

- Use one generic LSP tool family.
- Keep only tools that are useful for a coding agent, even if that means removing current clangd-only tools.
- Detect project root and local environment automatically.
- Backward compatibility is not required.
- Keep the current rename pattern: return a `WorkspaceEdit`, do not apply edits automatically.
- Add integration tests against a real `pyright-langserver`.
- Add diagnostics support.
- Keep backend abstraction if it stays cheap.

## Goals

- Give the agent one consistent navigation and analysis surface across Python and C/C++.
- Improve behavior on real editing tasks, not only synthetic unit tests.
- Minimize tool noise by exposing only high-value operations.
- Preserve the current safety model: tools analyze or propose edits, they do not silently mutate files.
- Make workspace and environment selection automatic from the path the agent is already operating on.

## Non-Goals

- Full editor parity with every LSP feature.
- Automatic application of rename edits.
- Supporting every language server generically in v1.
- Building a full file-watcher or incremental sync daemon outside of tool calls.

## Current State

The repo already has a mostly generic protocol layer in `src/agent/lsp/`, but the public API is still specialized around `ClangdClient` and `clangd_*` tools.

What is already reusable:

- JSON-RPC framing and async request handling.
- Document open/change tracking.
- Symbol, location, edit, and diagnostic typed dicts.
- Tool formatting helpers and path-safety checks.

What is currently too specialized:

- Client naming and singleton lifecycle are clangd-specific.
- Startup configuration assumes one subprocess and one backend.
- Tool names and docstrings are C/C++ specific.
- `workspace_symbol` is not anchored to a file path, so a generic backend cannot be selected safely.
- Real-task document freshness is weak: the client opens files lazily, but it does not reliably resync after the agent edits files on disk.

## Proposed Tool Surface

Expose this generic tool family in `src/agent/tools/tools_lsp.py`:

- `lsp_definition(path, line, character)`
- `lsp_references(path, line, character, include_declaration=True)`
- `lsp_document_symbols(path)`
- `lsp_workspace_symbols(path, query, language="")`
- `lsp_rename(path, line, character, new_name)`
- `lsp_diagnostics(path)`

### Why these tools

These six operations have the highest value for an autonomous coding agent:

- `definition`: navigate from usage to implementation.
- `references`: estimate blast radius and find callers/usages.
- `document_symbols`: understand local structure before editing.
- `workspace_symbols`: find candidate definitions without broad grep.
- `rename`: propose precise cross-file edits without applying them automatically.
- `diagnostics`: validate edits and discover semantic/type errors.

### Tools removed from the current clangd surface

Do not keep these in v1:

- `completion`
- `call_hierarchy`
- `type_hierarchy`

Reason:

- They add API and test surface.
- Cross-server support is less consistent.
- They are lower-value for the agent than definition, references, symbols, rename, and diagnostics.
- Completion is especially noisy and duplicates capabilities the model already has.

## API Shape

### Path-anchored server selection

Every generic LSP tool should take a `path` anchor.

- For file-scoped operations, `path` is the file being queried.
- For workspace-scoped symbol search, `path` is an anchor inside the intended project, used to choose the project root and backend.

`lsp_workspace_symbols` needs this change because `query` alone is insufficient once multiple backends exist.

### Optional language override

`lsp_workspace_symbols` should accept `language=""` as an optional override.

- Default: infer backend from `path`.
- Override: use the requested backend when the path is ambiguous.

The other tools do not need an explicit `language` parameter because the file extension is enough.

## Architecture

## 1. Generic client core

Refactor `src/agent/lsp/client.py` from `ClangdClient` into a generic `LanguageServerClient`.

Responsibilities:

- Spawn one language-server subprocess.
- Perform `initialize` / `initialized` handshake.
- Serialize requests.
- Track server capabilities.
- Maintain document versions.
- Store latest diagnostics per URI.
- Expose generic methods for the selected tool set.

Example public methods:

- `definition(path, line, character)`
- `references(path, line, character, include_declaration=True)`
- `document_symbol(path)`
- `workspace_symbol(query)`
- `rename(path, line, character, new_name)`
- `get_diagnostics(path)`
- `sync_document(path)`
- `await_diagnostics(path, timeout=...)`

The protocol implementation stays mostly unchanged. The main change is that backend-specific behavior moves into a spec object rather than being hard-coded.

## 2. Backend registry

Add a lightweight backend registry, for example in `src/agent/lsp/registry.py`.

Each backend spec should define:

- `server_id`: `"clangd"`, `"pyright"`
- `command`
- `args`
- `language_ids`
- `file_extensions`
- `root_markers`
- `supports_workspace_symbols`
- `supports_rename`
- `supports_diagnostics`
- optional configuration callback after initialization

Initial backends:

- clangd
  - command: env `CLANGD_PATH` or `clangd`
  - args: current clangd defaults
  - extensions: `.c`, `.cc`, `.cpp`, `.cxx`, `.h`, `.hpp`, `.hh`, `.hxx`
  - markers: `compile_commands.json`, `compile_flags.txt`, `.clangd`, `.git`
- pyright
  - command: env `PYRIGHT_LANGSERVER_PATH` or `pyright-langserver`
  - args: `--stdio`
  - extensions: `.py`, `.pyi`
  - markers: `pyrightconfig.json`, `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `.git`

This keeps the abstraction cheap: two concrete specs, one shared client.

## 3. Server manager

Replace the current single global client with a manager keyed by `(server_id, workspace_root)`.

Why:

- A generic tool family can target multiple languages.
- The agent may operate across more than one project root in the same process.
- Pyright and clangd need different subprocesses.

Manager behavior:

- Resolve backend from path.
- Detect workspace root.
- Reuse a running client for the same backend/root pair.
- Stop all clients during application shutdown.

## 4. Tool layer

Create `src/agent/tools/tools_lsp.py` and remove `tools_clangd.py` from the exported tool list.

Tool layer responsibilities:

- Validate path safety.
- Resolve backend and root through the manager.
- Synchronize the current document before requests.
- Format server responses into compact JSON for the model.
- Convert LSP positions to the existing 1-based line / 0-based character convention.
- Return `"Error: ..."` strings on failure, consistent with current behavior.

## Project Root Detection

Root detection should be path-driven and deterministic.

Algorithm:

1. Start from the anchor path.
2. If the anchor is a file, begin at its parent directory.
3. Walk upward toward filesystem root.
4. Prefer the nearest directory that matches language-specific markers.
5. Fallback to the nearest `.git`.
6. Final fallback: current working directory.

Language-specific preference:

- Python: prefer directories containing `pyrightconfig.json` or `pyproject.toml`; otherwise accept common project markers like `setup.py`, `setup.cfg`, or `requirements.txt`.
- C/C++: prefer directories containing `compile_commands.json`, `compile_flags.txt`, or `.clangd`.

This is better than the current global `cwd` behavior because it makes tool selection local to the file the agent is actually modifying.

## Python Environment Detection

Pyright should analyze using the project-local environment when possible.

### Detection rules

When no explicit pyright project config is present:

1. Prefer a local interpreter at one of:
   - `<root>/.venv/bin/python`
   - `<root>/venv/bin/python`
   - `<root>/env/bin/python`
2. Otherwise use `sys.executable` from the running agent process.
3. Optionally derive `python.venvPath` if the interpreter is in a conventional venv directory.

### Configuration sent to pyright

After initialization, send configuration equivalent to:

- `python.pythonPath`: detected interpreter path
- `python.analysis.diagnosticMode`: `workspace`
- `python.analysis.autoSearchPaths`: `true`
- `python.analysis.useLibraryCodeForTypes`: `true`

### Important constraint

If the project contains `pyrightconfig.json` or `pyproject.toml` with `[tool.pyright]`, pyright treats project config as authoritative and may ignore client-sent settings. The implementation must respect this.

Design implication:

- Environment detection is best-effort, not absolute.
- Repo-owned pyright config wins.
- The design should document this clearly in README and tool docstrings.

## Document Synchronization

This is the main real-task correctness gap in the current implementation.

Today, a file can be opened once in the server and then drift out of sync after the agent edits it through filesystem tools.

Fix:

- Add `sync_document(path)` to the generic client.
- Before every file-targeted request, read the current file contents from disk.
- If the document is unopened, send `didOpen`.
- If the text changed since the last sync, send `didChange` with whole-document replacement.
- Track a cheap fingerprint such as content hash or full text equality, not only version counters.

This keeps the LSP view aligned with disk state without requiring file watchers.

## Diagnostics Design

Add `lsp_diagnostics(path)`.

Behavior:

1. Validate path.
2. Resolve backend and root.
3. Sync the document.
4. Wait briefly for fresh diagnostics for that URI.
5. Return the latest diagnostics as compact JSON.

Output shape:

```json
[
  {
    "path": "/abs/path/file.py",
    "line": 12,
    "character": 8,
    "severity": "error",
    "source": "Pyright",
    "code": "reportArgumentType",
    "message": "Argument of type \"int\" cannot be assigned to parameter of type \"str\""
  }
]
```

Freshness strategy:

- Reset a per-document diagnostics event before `didOpen`/`didChange`.
- Wait for the next `publishDiagnostics` for that URI, with timeout.
- If no notification arrives before timeout, return the last known diagnostics.

This is sufficient for a coding agent and avoids building a more complex server-idle protocol.

## Capability Handling

The generic client should read initialize capabilities and fail clearly when a backend does not support a requested feature.

Example:

- If `workspaceSymbolProvider` is missing, `lsp_workspace_symbols` returns `Error: workspace symbols are not supported by pyright`.

This keeps the abstraction honest and makes it easy to add more backends later.

## Response Formatting

Reuse the existing JSON formatting patterns from `tools_clangd.py` where possible.

Keep:

- 1-based line numbers in outputs
- 0-based character offsets
- path normalization from `file://` URIs
- output truncation guard
- flattening of document symbols for easier model consumption

Add:

- severity name mapping for diagnostics
- backend name in error messages when useful

## Configuration Changes

Minimal config additions:

- `PYRIGHT_LANGSERVER_PATH` default `pyright-langserver`

Keep:

- `CLANGD_PATH`

No new top-level `Settings` dataclass fields are required unless the repo wants all tool runtime knobs centralized later. For this change, environment lookup inside the LSP subsystem is sufficient and keeps scope contained.

## File-Level Changes

Expected code movement:

- `src/agent/lsp/client.py`
  - generalize client class
  - keep framing and generic request logic
  - add document sync and diagnostics wait support
- `src/agent/lsp/__init__.py`
  - export generic client and manager entrypoints
- `src/agent/lsp/registry.py`
  - backend specs and root detection
- `src/agent/tools/tools_lsp.py`
  - new generic tools
- `src/agent/tools/tools.py`
  - register new tools, remove clangd-specific ones
- `src/agent/tools/__init__.py`
  - export new tool names
- `src/agent/__main__.py`
  - stop all managed LSP clients on shutdown
- `README.md`
  - document new tool family and `PYRIGHT_LANGSERVER_PATH`

Optional cleanup after migration:

- remove or rename `tools_clangd.py`
- rename `ClangdClient` references in tests and docs

## Testing Strategy

## 1. Keep protocol-level mock tests

Keep mock-server tests for:

- JSON-RPC framing
- request/response ordering
- failure handling
- response normalization
- capability gating

Reason:

- These are fast and isolate protocol bugs that real-server integration tests do not diagnose well.

## 2. Add real-server integration tests for pyright

Add integration coverage gated on `pyright-langserver` availability.

Suggested fixture project: `tests/fixtures/lsp_python/`

Contents:

- small multi-file package
- at least one import relationship
- one known type error for diagnostics
- one symbol suitable for rename and references

Required integration tests:

- `lsp_definition` resolves a symbol across Python files
- `lsp_references` returns at least the declaration and a usage
- `lsp_document_symbols` returns class/function structure
- `lsp_workspace_symbols` finds a known symbol
- `lsp_rename` returns a non-empty `WorkspaceEdit`
- `lsp_diagnostics` returns the expected pyright diagnostic

## 3. Keep real-server integration tests for clangd

Retain and adapt the existing clangd integration tests to the generic tool names.

Required coverage:

- definition
- workspace symbols
- diagnostics

Diagnostics should be added here as part of the migration because clangd also publishes them.

## 4. Add root/env detection tests

Add focused tests for backend and root selection.

Cases:

- `.py` file resolves to pyright
- `.cpp` file resolves to clangd
- nearest project marker wins over outer `.git`
- Python interpreter detection prefers local `.venv`
- explicit pyright project config suppresses client-side assumptions

These can be unit tests around the registry and manager, plus at least one integration test proving pyright works on a project-local environment.

## Migration Plan

1. Generalize the client without changing framing internals.
2. Add backend registry and manager.
3. Implement generic tools.
4. Update tool registration and exports.
5. Port existing clangd tests to generic names.
6. Add real pyright integration tests.
7. Update README.
8. Remove old clangd-specific public tool names.

## Risks And Mitigations

### Risk: pyright configuration precedence surprises users

Cause:

- project `pyrightconfig.json` or `[tool.pyright]` overrides client settings.

Mitigation:

- document precedence clearly
- keep client configuration best-effort
- test both configured and unconfigured Python fixtures if needed

### Risk: stale server state after file edits

Cause:

- LSP document state diverges from disk.

Mitigation:

- mandatory `sync_document(path)` before file-targeted requests

### Risk: workspace symbol behavior differs by backend

Cause:

- indexing and symbol support vary across servers.

Mitigation:

- capability checks
- path-anchored backend selection
- integration tests against both real servers

### Risk: startup latency from multiple subprocesses

Cause:

- one client per backend/root pair.

Mitigation:

- lazy start
- reuse by backend/root cache
- best-effort shutdown cleanup

## Recommendation

Implement the generic tool family with six tools only: definition, references, document symbols, workspace symbols, rename, and diagnostics. Generalize the current client into a backend-agnostic core, keep the abstraction cheap through two explicit backend specs, and make document synchronization a first-class part of the design. That yields the biggest practical improvement for real coding-agent tasks while keeping scope controlled.

## Assumptions

- `pyright-langserver` is available as a stdio language server via `pyright-langserver --stdio`.
- Both backends can be run safely as long-lived subprocesses in the agent process.
- The agent continues to treat LSP tools as read-mostly analysis tools, with rename returning edits but not applying them.# Generic LSP Tool Family For C/C++ And Python

## Status

Proposed.

## Agreed Decisions

- Replace the clangd-specific public tool surface with one generic LSP tool family.
- Keep only the LSP operations that are high-value for a coding agent.
- Detect project root and local environment automatically.
- Do not preserve backward compatibility with the current `clangd_*` tool names.
- Keep rename behavior analysis-only: return a `WorkspaceEdit`, do not apply edits automatically.
- Add real integration tests for `pyright-langserver`; mocks are not sufficient on their own.
- Add diagnostics support.
- Keep the internal abstraction open enough to support more language servers later if the extra complexity stays low.

## Problem

The current implementation in `src/agent/lsp/` and `src/agent/tools/tools_clangd.py` is nominally LSP-based but operationally clangd-specific:

- the main client type is `ClangdClient`
- the singleton model assumes one global server process
- the public tools are tied to clangd naming
- the supported feature set reflects clangd capabilities more than agent needs
- there is no Python LSP path even though `pyright-langserver` is available in the environment

This blocks direct use of LSP navigation and diagnostics inside this Python project.

## Goals

- Provide one generic LSP tool family that works for both C/C++ and Python.
- Support the most useful agent workflows: navigation, symbol discovery, rename planning, and diagnostics.
- Auto-select the correct server from the target file.
- Auto-detect workspace root and Python local environment with sensible defaults.
- Preserve the current tool contract style: JSON payload on success, `Error: ...` string on failure.
- Keep real-server integration tests for both clangd and pyright.

## Non-Goals

- No attempt to keep `clangd_*` public tool names alive.
- No automatic application of LSP edits.
- No first-version support for completion, call hierarchy, or type hierarchy.
- No attempt to build a full editor-grade LSP feature surface.

## Why These Tools And Not Others

The useful first-version tools for a coding agent are:

- `lsp_definition`
- `lsp_references`
- `lsp_document_symbols`
- `lsp_workspace_symbols`
- `lsp_rename`
- `lsp_diagnostics`

These cover the agent workflows that matter most:

- find where code is defined
- find impact of a change
- inspect file and workspace structure
- prepare multi-file rename edits safely
- detect static-analysis failures after edits

The following current clangd tools should be removed from the public surface:

- completion
- call hierarchy
- type hierarchy

Reason:

- completion is lower-value for an autonomous coding agent than direct symbol lookup and diagnostics
- call/type hierarchy are specialized, add complexity, and are not required for the main coding loop
- pyright parity would be uneven for these features, which makes the generic family less coherent

## Proposed Public Tool API

Create a new module `src/agent/tools/tools_lsp.py` and expose these tools:

### `lsp_definition(path: str, line: int, character: int) -> str`

Returns a JSON array of locations:

```json
[
  {
    "path": "/abs/path/file.py",
    "line": 12,
    "character": 4
  }
]
```

### `lsp_references(path: str, line: int, character: int, include_declaration: bool = true) -> str`

Returns a JSON array of locations in the same normalized format.

### `lsp_document_symbols(path: str) -> str`

Returns a flattened JSON array of symbols with depth information.

### `lsp_workspace_symbols(path: str, query: str) -> str`

`path` anchors language selection and workspace-root detection. This avoids ambiguity for a generic tool family.

Returns a JSON array of workspace symbols.

### `lsp_rename(path: str, line: int, character: int, new_name: str) -> str`

Returns the raw `WorkspaceEdit` JSON object, mirroring the current analysis-only rename pattern.

### `lsp_diagnostics(path: str) -> str`

Returns a JSON array of normalized diagnostics:

```json
[
  {
    "path": "/abs/path/file.py",
    "line": 8,
    "character": 12,
    "end_line": 8,
    "end_character": 17,
    "severity": "error",
    "code": "reportAssignmentType",
    "source": "Pyright",
    "message": "Expression of type ..."
  }
]
```

## Position And Output Conventions

- Inputs keep the current convention: 1-based lines, 0-based character offsets.
- Outputs keep 1-based lines.
- All tool results remain JSON strings.
- All tool failures remain `Error: ...` strings.
- Path safety remains enforced for any path argument that targets a file inside the repo.

## Architecture

## 1. Replace `ClangdClient` With A Generic `LspClient`

Refactor `src/agent/lsp/client.py` into a language-server-neutral client.

Core changes:

- rename `ClangdClient` to `LspClient`
- remove clangd-specific log strings and error messages from the core client
- parameterize executable path and command-line args
- retain the existing JSON-RPC framing and sequential request model
- retain document tracking and diagnostics storage

The generic client continues to implement:

- `did_open`
- `did_change`
- `did_close`
- `ensure_open`
- `definition`
- `references`
- `document_symbol`
- `workspace_symbol`
- `rename`

Add diagnostics-specific waiting support:

- track the latest published diagnostic version or generation per URI
- allow `lsp_diagnostics` to wait briefly for an initial publication after `didOpen`
- use a bounded wait window, for example 2 seconds with polling/event wakeup

This is needed because diagnostics are push-based and may arrive after the request-driving tool call.

## 2. Add A Lightweight Server Registry

Add a new internal module, for example `src/agent/lsp/registry.py`, with a small declarative registry.

Suggested shape:

```python
@dataclass(frozen=True)
class ServerSpec:
    server_id: str
    language_id: str
    command: tuple[str, ...]
    file_extensions: frozenset[str]
    root_markers: tuple[str, ...]
    build_settings: Callable[[WorkspaceContext], dict[str, Any]]
```

Initial entries:

### Python

- `server_id = "python"`
- `language_id = "python"`
- `command = (resolved_pyright_langserver, "--stdio")`
- file extensions: `.py`, `.pyi`
- root markers:
  - `pyrightconfig.json`
  - `pyproject.toml`
  - `setup.py`
  - `setup.cfg`
  - `requirements.txt`
  - `.venv`
  - `.git`

### C/C++

- `server_id = "cpp"`
- `language_id = "cpp"`
- `command = (resolved_clangd, "--background-index", "--clang-tidy=0", "--header-insertion=never")`
- file extensions: `.c`, `.cc`, `.cpp`, `.cxx`, `.h`, `.hh`, `.hpp`, `.hxx`
- root markers:
  - `compile_commands.json`
  - `compile_flags.txt`
  - `.clangd`
  - `.git`

This keeps the abstraction open without over-designing it.

## 3. Replace The Single Global Client With A Client Pool

The current singleton model is insufficient once Python and C/C++ are both supported.

Introduce a process-wide pool keyed by:

- `server_id`
- `workspace_root`

Example key:

```text
("python", "/workspaces/coding_agent")
```

Behavior:

- first tool call for a `(server_id, workspace_root)` pair creates and starts a client
- later calls reuse that client
- shutdown cleans up all pooled clients in `agent.__main__`

This preserves the lazy-start behavior while allowing multiple workspaces and languages.

## 4. Add Workspace Context Detection

Add a new internal helper, for example `src/agent/lsp/workspace.py`, responsible for:

- validating the target path
- determining the language from extension
- finding the workspace root
- detecting Python interpreter / local environment

Suggested result object:

```python
@dataclass(frozen=True)
class WorkspaceContext:
    path: Path
    language: str
    workspace_root: Path
    python_executable: Path | None = None
    python_venv_path: Path | None = None
    has_pyright_config: bool = False
```
```

### Root Detection Algorithm

For a given target file:

1. Resolve the file path.
2. Walk upward from the file’s directory.
3. Stop at the nearest directory containing a language-specific root marker.
4. If none is found, fall back to the nearest `.git` ancestor.
5. If `.git` is absent, fall back to the current process working directory.

This keeps behavior stable for both repo roots and nested subprojects.

## Python Environment Detection

For Python paths only:

1. If a local virtualenv exists under the chosen workspace root at `.venv`, `venv`, or `env`, prefer its interpreter.
2. Otherwise use `sys.executable` from the running agent process.
3. Record whether `pyrightconfig.json` or `[tool.pyright]` exists.

Rationale:

- this repo already uses a local `.venv`
- the agent usually runs inside the intended interpreter already
- project config should win when it exists

## 5. Pyright Configuration Strategy

Start `pyright-langserver` with `--stdio`.

After initialization, send `workspace/didChangeConfiguration` with fallback editor-style settings only when project config does not already prescribe them.

Base fallback settings:

```json
{
  "python": {
    "pythonPath": "<detected interpreter>",
    "venvPath": "<detected venv parent or omitted>"
  },
  "python.analysis": {
    "diagnosticMode": "workspace",
    "autoSearchPaths": true,
    "useLibraryCodeForTypes": true,
    "typeCheckingMode": "standard"
  }
}
```

Rules:

- if `pyrightconfig.json` exists, treat it as authoritative
- if only `[tool.pyright]` exists in `pyproject.toml`, treat it as authoritative
- fallback settings are for projects with no pyright config
- `python.pythonPath` should be sent whenever an interpreter is confidently detected because it is the most direct signal for local-environment resolution

This matches pyright’s documented behavior: project config takes precedence, editor settings fill the gap when config is absent.

## 6. Tool Routing

Each generic tool should follow the same internal flow:

1. resolve and validate `path`
2. build `WorkspaceContext`
3. pick `ServerSpec` from the extension
4. acquire `LspClient` from the pool
5. open the file if needed
6. execute the LSP request or read cached diagnostics
7. normalize response into compact JSON

`lsp_workspace_symbols` uses `path` to perform steps 1 through 4 before issuing `workspace/symbol`.

## Response Normalization

Normalization should move out of the clangd-specific tool module and become generic.

Keep the existing useful helpers, generalized as needed:

- location formatting
- hierarchical symbol flattening
- workspace-symbol formatting
- payload truncation

Add diagnostics normalization:

- map numeric severity to strings: `error`, `warning`, `information`, `hint`
- surface code, source, message
- include both start and end positions
- include related information only if present and cheap to serialize

## 7. Public Module Changes

Replace the current exports:

- remove `src/agent/tools/tools_clangd.py`
- add `src/agent/tools/tools_lsp.py`
- update `src/agent/tools/__init__.py`
- update `src/agent/tools/tools.py`

Public tool names become:

- `lsp_definition`
- `lsp_references`
- `lsp_document_symbols`
- `lsp_workspace_symbols`
- `lsp_rename`
- `lsp_diagnostics`

No aliases for the old clangd names.

## 8. Error Handling

The generic tools should keep the current failure contract.

Examples:

- unsupported extension: `Error: No configured LSP server for file extension '.md'`
- missing binary: `Error: pyright-langserver not found on PATH`
- out-of-repo path: `Error: Path is not inside the project folder`

The core client should raise structured Python exceptions; the tool layer should convert them into user-facing strings.

## Testing Plan

## Unit Tests

Refactor the current tests to match the new generic surface.

Add unit coverage for:

- server selection by file extension
- root detection
- Python interpreter detection
- diagnostics normalization
- tool registration
- pooled-client lifecycle
- error messages for missing binaries and unsupported file types

Mocks remain useful here for fast protocol-level and routing-level coverage.

## Real-Server Integration Tests

Keep the existing real clangd integration tests and rename them to the generic tool names.

Add new integration tests for `pyright-langserver`.

Create a new fixture project, for example `tests/fixtures/lsp_python/`, containing:

- a small package with at least two modules
- a definition/reference target
- a rename target
- a deliberate type error for diagnostics
- a minimal `pyproject.toml` with `[tool.pyright]` or a `pyrightconfig.json`

Recommended integration coverage:

### `lsp_definition`

- from a call site in one module, resolve to the function or class definition in another module

### `lsp_references`

- find all references of a symbol across at least two files

### `lsp_document_symbols`

- list top-level functions/classes from a Python file

### `lsp_workspace_symbols`

- find a symbol by name within the Python fixture workspace

### `lsp_rename`

- ensure returned `WorkspaceEdit` includes all affected URIs

### `lsp_diagnostics`

- open a file containing a deterministic type error and assert that pyright publishes a diagnostic with the expected message/code/source

Implementation notes:

- `pyright-langserver` publishes diagnostics asynchronously, so tests should use retry loops with short sleeps like the current clangd integration tests
- tests should be marked `integration`
- local runs may skip if the binary is absent, but the intended steady state is that development and CI environments install both clangd and pyright-langserver

## Migration Plan

## File-Level Changes

- refactor `src/agent/lsp/client.py` into a generic client
- update `src/agent/lsp/__init__.py` exports
- add `src/agent/lsp/registry.py`
- add `src/agent/lsp/workspace.py`
- replace `src/agent/tools/tools_clangd.py` with `src/agent/tools/tools_lsp.py`
- update `src/agent/tools/__init__.py`
- update `src/agent/tools/tools.py`
- update `src/agent/__main__.py` cleanup to reset the client pool rather than a single client
- rewrite `tests/test_lsp_client.py` and `tests/test_lsp_tools.py` around generic naming
- add `tests/fixtures/lsp_python/`
- update `README.md` configuration and tooling docs

## Rollout Sequence

1. Generalize the client and singleton into a pool without changing tool behavior yet.
2. Add workspace detection and registry.
3. Introduce the new generic tool module.
4. Switch tool exports and registrations.
5. Remove the clangd-only tool module and obsolete tests.
6. Add pyright integration tests.
7. Update docs.

## Risks And Mitigations

## Pyright Configuration Ambiguity

Risk:

- pyright can derive behavior from config files, editor settings, and interpreter selection

Mitigation:

- make project config authoritative
- use fallback settings only when project config is absent
- keep environment detection simple and explicit

## Diagnostics Timing

Risk:

- diagnostics are asynchronous and can race tool calls or tests

Mitigation:

- track diagnostic publications per URI
- add bounded waits in the diagnostics tool and retry loops in integration tests

## Over-Generalization

Risk:

- a large abstraction could slow delivery without real benefit

Mitigation:

- keep the registry minimal
- support only Python and C/C++ initially
- support only the six agreed tool operations

## Assumptions

- `pyright-langserver` is started as `pyright-langserver --stdio`.
- the agent continues to run inside the intended project interpreter in normal development workflows.
- `.venv` remains the common local-environment name when no explicit pyright config exists.
- no other current code depends on the old `clangd_*` public tool names.

## Result

The result is one small generic LSP layer with:

- one public tool family
- one generic client implementation
- one pooled lifecycle model
- automatic routing for Python and C/C++
- diagnostics as a first-class agent capability
- real integration coverage for both clangd and pyright

This is the smallest change that makes LSP actually useful for the agent in its own Python codebase while keeping the design extensible.