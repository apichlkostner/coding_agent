# Implementation Plan

## Source Of Truth

`DESIGN.md` is the source of truth for scope and behavior. This plan converts that design into delivery slices. It does not change the design.

## Scope Summary

Implement a generic LSP tool family for C/C++ and Python with these public tools:

- `lsp_definition`
- `lsp_references`
- `lsp_document_symbols`
- `lsp_workspace_symbols`
- `lsp_rename`
- `lsp_diagnostics`

Remove the current clangd-specific public tools from the exported tool surface. Keep rename analysis-only. Add real integration coverage for `pyright-langserver` and retain real-server coverage for clangd.

## Files Expected To Change

Core runtime:

- `src/agent/lsp/client.py`
- `src/agent/lsp/__init__.py`
- `src/agent/lsp/types.py`
- `src/agent/lsp/registry.py` new
- `src/agent/lsp/workspace.py` new or merged into `registry.py`

Tool layer:

- `src/agent/tools/tools_clangd.py` remove or replace
- `src/agent/tools/tools_lsp.py` new
- `src/agent/tools/tools.py`
- `src/agent/tools/__init__.py`

Lifecycle and docs:

- `src/agent/__main__.py`
- `README.md`

Tests and fixtures:

- `tests/test_lsp_client.py`
- `tests/test_lsp_tools.py`
- `tests/fixtures/lsp_cpp/` updates as needed
- `tests/fixtures/lsp_python/` new

## Delivery Strategy

Implement in vertical slices that preserve a working state after each slice. Prefer landing backend-neutral infrastructure first, then generic tool wiring, then Python support, then cleanup.

## Slice 1: Generalize Client Core

### Goal

Turn the clangd-specific client into a backend-neutral language-server client without changing framing internals.

### Changes

- Rename `ClangdClient` to a generic name such as `LanguageServerClient`.
- Replace clangd-specific constructor and logging assumptions with backend-neutral process configuration.
- Keep stdio JSON-RPC framing, request locking, and notification handling unchanged.
- Preserve existing methods needed by the final tool set:
  - definition
  - references
  - document_symbol
  - workspace_symbol
  - rename
- Remove or de-emphasize methods that are no longer part of the public tool surface only after generic tools are in place, to avoid destabilizing the migration.
- Extend `_DocumentState` so the client can detect file-content drift, not only version increments.
- Add `sync_document(path)` to ensure server document state matches disk before file-targeted requests.
- Add diagnostics freshness support:
  - per-URI diagnostics event or generation tracking
  - `await_diagnostics(path, timeout=...)`

### Files

- `src/agent/lsp/client.py`
- `src/agent/lsp/__init__.py`
- `src/agent/lsp/types.py` only if diagnostics/result typing needs small additions

### Verification

- Update protocol-level unit tests in `tests/test_lsp_client.py` to use the generic client name.
- Add unit coverage for:
  - `sync_document` sends `didOpen` on first access
  - `sync_document` sends `didChange` only when file contents changed
  - diagnostics event/generation tracking wakes waiters on `publishDiagnostics`
- Run: `uv run pytest tests/test_lsp_client.py`

### Exit Criteria

- Client is backend-neutral by API and logging.
- Existing mock-server protocol tests still pass.
- Diagnostics can be awaited deterministically in tests.

## Slice 2: Add Backend Registry And Workspace Resolution

### Goal

Introduce explicit backend selection, project-root detection, Python environment detection, and pooled client lifecycle.

### Changes

- Add a lightweight registry for clangd and pyright backend specs.
- Add path-driven backend resolution from file extension.
- Add language-specific root-marker detection.
- Add Python interpreter detection with preference order from `DESIGN.md`:
  - `<root>/.venv/bin/python`
  - `<root>/venv/bin/python`
  - `<root>/env/bin/python`
  - fallback to `sys.executable`
- Detect presence of `pyrightconfig.json` or `[tool.pyright]` in `pyproject.toml`.
- Add a manager keyed by `(server_id, workspace_root)` that lazily creates and caches clients.
- Add a bulk reset/shutdown entrypoint for all managed clients.

### Files

- `src/agent/lsp/registry.py` new
- `src/agent/lsp/workspace.py` new or folded into `registry.py`
- `src/agent/lsp/__init__.py`
- `src/agent/lsp/client.py`
- `src/agent/__main__.py`

### Verification

- Add unit coverage for:
  - `.py` resolves to pyright
  - `.cpp` resolves to clangd
  - nearest language-specific root marker wins over outer `.git`
  - Python interpreter detection prefers local `.venv`
  - manager reuses the same backend/root client
  - reset stops all pooled clients
- Run targeted tests: `uv run pytest tests/test_lsp_client.py -k 'registry or workspace or manager or diagnostics'`

### Exit Criteria

- Code can resolve backend, root, and environment from a path.
- Multiple backend/root combinations can coexist in one process.
- Shutdown path no longer assumes a single global clangd client.

## Slice 3: Introduce Generic Tool Surface For Clangd-Backed Paths

### Goal

Replace exported clangd-specific tools with the generic six-tool family while keeping C/C++ behavior working first.

### Changes

- Create `src/agent/tools/tools_lsp.py`.
- Port reusable formatting helpers from `tools_clangd.py`:
  - URI to path conversion
  - location formatting
  - document symbol flattening
  - workspace symbol formatting
  - payload truncation
- Implement the generic public tools:
  - `lsp_definition`
  - `lsp_references`
  - `lsp_document_symbols`
  - `lsp_workspace_symbols`
  - `lsp_rename`
  - `lsp_diagnostics`
- Ensure all file-targeted requests call `sync_document(path)` before issuing LSP requests.
- Enforce capability checks and return `Error: ...` strings when unsupported.
- Update `get_tools()` and package exports to use only the new generic tool names.
- Keep `tools_clangd.py` only as a temporary migration crutch if needed within the slice; remove it from public exports immediately.

### Files

- `src/agent/tools/tools_lsp.py` new
- `src/agent/tools/tools.py`
- `src/agent/tools/__init__.py`
- `src/agent/tools/tools_clangd.py` remove or stop exporting

### Verification

- Rewrite tool-layer mock tests in `tests/test_lsp_tools.py` to target the generic names.
- Add registration assertions for the six generic tool names only.
- Add unit coverage for:
  - `lsp_workspace_symbols` path-anchored backend selection
  - unsupported extension returns a clear error
  - unsupported capability returns a clear error
  - path safety still rejects out-of-project paths
- Run: `uv run pytest tests/test_lsp_tools.py -k 'not integration'`

### Exit Criteria

- The public tool surface exposes only the generic six tools.
- Clangd-backed file paths still work through the new generic tools.
- No exported code path depends on `clangd_*` tool names.

## Slice 4: Add Diagnostics End To End And Rebaseline Clangd Integration

### Goal

Make diagnostics first-class in the generic API and prove the new tool family works against the real clangd server.

### Changes

- Finish diagnostics formatting in `tools_lsp.py`:
  - severity mapping
  - code/source/message fields
  - line/character normalization
- Rework clangd integration tests to call generic tools.
- Extend the C++ fixture only if needed to produce deterministic diagnostics.

### Files

- `src/agent/tools/tools_lsp.py`
- `tests/test_lsp_tools.py`
- `tests/fixtures/lsp_cpp/` if a stable diagnostics case is needed

### Verification

- Real clangd integration tests should cover:
  - `lsp_definition`
  - `lsp_workspace_symbols`
  - `lsp_diagnostics`
- Run: `uv run pytest tests/test_lsp_tools.py -m integration -k clangd`

### Exit Criteria

- Diagnostics are part of the generic API.
- Real clangd coverage exists for the migrated tool family.

## Slice 5: Add Pyright Backend Configuration And Python Integration Tests

### Goal

Enable the same generic tools for Python through `pyright-langserver` with project-root and environment detection.

### Changes

- Add pyright backend spec with `--stdio` startup.
- Implement post-initialize configuration for pyright when repo-owned pyright config is absent.
- Send best-effort settings derived from the detected environment:
  - `python.pythonPath`
  - `python.analysis.diagnosticMode = workspace`
  - `python.analysis.autoSearchPaths = true`
  - `python.analysis.useLibraryCodeForTypes = true`
- Respect pyright config precedence when `pyrightconfig.json` or `[tool.pyright]` is present.
- Add a Python fixture workspace with:
  - multi-file definitions/references
  - a deterministic rename target
  - a deterministic type error for diagnostics
  - project-local config or interpreter layout that exercises root/env detection

### Files

- `src/agent/lsp/registry.py`
- `src/agent/lsp/client.py`
- `src/agent/tools/tools_lsp.py`
- `tests/test_lsp_tools.py`
- `tests/fixtures/lsp_python/` new

### Verification

- Real pyright integration tests should cover:
  - `lsp_definition`
  - `lsp_references`
  - `lsp_document_symbols`
  - `lsp_workspace_symbols`
  - `lsp_rename`
  - `lsp_diagnostics`
- Add at least one test proving project-local environment selection works for Python.
- Run: `uv run pytest tests/test_lsp_tools.py -m integration -k pyright`

### Exit Criteria

- Python paths route through pyright successfully.
- Real pyright integration coverage passes.
- Diagnostics and rename work for Python through the same generic tool surface.

## Slice 6: Documentation And Cleanup

### Goal

Document the new generic LSP behavior and remove obsolete clangd-only names from the repo surface.

### Changes

- Update `README.md` configuration table:
  - add `PYRIGHT_LANGSERVER_PATH`
  - rewrite LSP/tooling docs around the generic names
- Update any remaining docstrings or comments that say the system is clangd-only.
- Remove dead exports, helpers, and tests for removed tools:
  - completion
  - call hierarchy
  - type hierarchy
- Delete `tools_clangd.py` if still present and unused.

### Files

- `README.md`
- `src/agent/tools/tools_clangd.py` remove if no longer referenced
- any remaining doc or test files with stale clangd-only naming

### Verification

- Run focused full suite for touched areas: `uv run pytest tests/test_lsp_client.py tests/test_lsp_tools.py`
- Run a broader regression pass if time allows: `uv run pytest`

### Exit Criteria

- No public docs or exports refer to the old clangd-specific tool family.
- The repo documents both clangd and pyright paths correctly.

## Cross-Slice Verification Matrix

After all slices:

- Unit: `uv run pytest tests/test_lsp_client.py tests/test_lsp_tools.py -k 'not integration'`
- Clangd integration: `uv run pytest tests/test_lsp_tools.py -m integration -k clangd`
- Pyright integration: `uv run pytest tests/test_lsp_tools.py -m integration -k pyright`
- Full targeted LSP pass: `uv run pytest tests/test_lsp_client.py tests/test_lsp_tools.py`

## Main Risks

### 1. Stale document state

Risk:

- Tools return results for old file contents after filesystem edits.

Plan response:

- Implement and test `sync_document(path)` before exposing generic tools.
- Treat this as a Slice 1 blocker, not cleanup.

### 2. Pyright configuration precedence

Risk:

- Detected interpreter/env settings are ignored when repo config exists.

Plan response:

- Encode precedence rules in the backend spec.
- Cover both configured and unconfigured Python fixture variants if one fixture is insufficient.

### 3. Capability mismatch across backends

Risk:

- Generic tools claim support that one backend does not actually provide.

Plan response:

- Gate each tool on initialize capabilities.
- Add negative-path tests for unsupported capability errors.

### 4. Test flakiness from async diagnostics and indexing

Risk:

- Real-server integration tests fail intermittently because diagnostics or workspace indexing arrive late.

Plan response:

- Reuse retry loops with bounded timeouts.
- Make diagnostics waiting explicit in the client.
- Keep fixture projects small.

### 5. Migration churn from naming changes

Risk:

- Residual `clangd_*` imports or cleanup hooks remain after migration.

Plan response:

- Switch exports early in Slice 3.
- Use a repo search before final cleanup to remove stale references.

## Suggested Execution Order

1. Slice 1
2. Slice 2
3. Slice 3
4. Slice 4
5. Slice 5
6. Slice 6

This order keeps the largest architectural change isolated first, then re-establishes working generic tools on clangd before adding pyright-specific behavior.