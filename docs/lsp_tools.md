# LSP Tools

## Overview

The agent provides six generic tools that speak the
[Language Server Protocol](https://microsoft.github.io/language-server-protocol/)
over stdio. The backend is selected from the target path:

| Backend | Language | Binary |
|---|---|---|
| `clangd` | C, C++ | `clangd` |
| `pyright` | Python | `pyright-langserver` |

| Tool | Purpose |
|---|---|
| `lsp_definition` | Go-to-definition for a symbol at a cursor position |
| `lsp_references` | Find all references to a symbol |
| `lsp_document_symbols` | List symbols in a single file |
| `lsp_workspace_symbols` | Search symbols across the workspace |
| `lsp_rename` | Return a `WorkspaceEdit` JSON for a rename (not applied) |
| `lsp_diagnostics` | Return diagnostics for a single file |

All tools return JSON strings; the LLM reasons over the raw LSP output.

The surface is intentionally narrow. Completion, call hierarchy, and type
hierarchy are **not** exposed. Each tool checks the server's advertised
capability first and returns an error string when the backend does not
support the request.

## Installation

### clangd (C / C++)

```bash
# Debian / Ubuntu
sudo apt install clangd

# macOS
brew install llvm

# Arch Linux
sudo pacman -S clang
```

Verify it works:

```bash
clangd --version
```

If clangd is not on `PATH`, set `CLANGD_PATH` to the full binary path
(e.g. `/usr/lib/llvm-18/bin/clangd`).

### pyright (Python)

`pyright-langserver` ships with the `pyright` dependency, so `uv sync`
installs it. Verify it works:

```bash
uv run pyright-langserver --version
```

If it is not on `PATH`, set `PYRIGHT_LANGSERVER_PATH` to the full binary path.

## Workspace root detection

The workspace root is the nearest ancestor directory containing a
backend-specific marker:

| Backend | Markers |
|---|---|
| `pyright` | `pyrightconfig.json`, `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `.venv` |
| `clangd` | `compile_commands.json`, `compile_flags.txt`, `.clangd` |

If no marker is found, the nearest `.git` directory is used; failing that,
the current working directory.

## compile_commands.json

For accurate C/C++ results, the project root needs a `compile_commands.json`.
clangd works without it (best-effort parsing) but misses macros, includes, and
type information.

Generate it from CMake:

```bash
cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
ln -s build/compile_commands.json .
```

Or with Bear for non-CMake projects:

```bash
bear -- make
```

## Python interpreter selection

For Python requests, the client prefers a project-local interpreter from
`.venv`, `venv`, or `env` under the workspace root, then falls back to the
running interpreter. Pyright settings (`python.pythonPath`, `python.venvPath`,
and analysis options) are injected via `workspace/didChangeConfiguration`
unless the workspace already provides its own pyright configuration
(`pyrightconfig.json` or a `[tool.pyright]` section in `pyproject.toml`).

## Position conventions

All tools accept **1-based line numbers** and **0-based character offsets**,
matching `treesitter_get_symbols` and common editor conventions (VS Code,
vim). Outputs are converted from LSP's 0-based to 1-based lines.

## Output format

Results are JSON strings capped at **8 000 characters**. When a result exceeds
this limit:

- **List outputs** (symbols, locations, diagnostics): a
  `{"truncated": true, "omitted_count": N}` object is appended.
- **Dict outputs** (rename): the string is truncated at the limit and suffixed
  with `"... (truncated)"`.

## Client lifecycle

Clients are pooled by `(backend, workspace root)` and started lazily on the
first tool call that resolves to that pair. A client is reused for the
lifetime of the agent process and stopped during shutdown. The first
invocation for a backend may take 2-5 seconds while the server starts and
indexes the project; subsequent calls are fast.

## Limitations

- **No auto-restart on config changes.** If you regenerate
  `compile_commands.json` or change pyright configuration while the agent is
  running, the server does not pick up the changes. Restart the agent to
  reload.
- **Workspace symbols need a warm file.** `lsp_workspace_symbols` may return
  empty results until at least one file has been opened. The tool syncs the
  anchor file before querying, but servers only index files that have been
  opened or queried.
- **Cursor placement matters for definition.** `lsp_definition` returns the
  symbol the cursor is *on*, not the nearest enclosing token. Place the cursor
  directly on the symbol name.
- **Diagnostics are best-effort.** `lsp_diagnostics` waits up to 2.5 seconds
  for a fresh `publishDiagnostics` notification after syncing the document,
  then returns whatever is cached.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CLANGD_PATH` | `clangd` | Path to the clangd binary |
| `PYRIGHT_LANGSERVER_PATH` | `pyright-langserver` | Path to the pyright language server binary |

## Troubleshooting

**Tool returns `"Error: clangd not found on PATH (looked for 'clangd')"`** —
clangd is not installed or not on `PATH`. Install it or set `CLANGD_PATH`.

**Tool returns `"Error: pyright not found on PATH (looked for 'pyright-langserver')"`** —
pyright is not installed or not on `PATH`. Run `uv sync` or set
`PYRIGHT_LANGSERVER_PATH`.

**Tool returns `"Error: Path is not inside the project folder"`** — the file is
outside the project root. Only files under the project root are accessible.

**Tool returns `"Error: <feature> are not supported by <backend>"`** — the
backend does not advertise the required capability. For example, clangd
without a `compile_commands.json` may not support workspace symbols.

**Workspace symbols return empty `[]`** — no file has been indexed yet. Call
another tool first (e.g. `lsp_document_symbols`) on a file in the project.
