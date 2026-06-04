# clangd LSP Tools

## Overview

The agent provides eight tools that use [clangd](https://clangd.llvm.org/) to
provide IDE-grade code intelligence for C, C++, and Objective-C codebases:

| Tool | Purpose |
|---|---|
| `clangd_completion` | Code completions at a cursor position |
| `clangd_definition` | Go-to-definition for a symbol |
| `clangd_references` | Find all references to a symbol |
| `clangd_document_symbols` | List symbols in a single file |
| `clangd_workspace_symbols` | Search symbols across the workspace |
| `clangd_rename` | Rename a symbol (returns `WorkspaceEdit` JSON) |
| `clangd_type_hierarchy` | Explore supertypes / subtypes of a type |
| `clangd_call_hierarchy` | Explore incoming / outgoing calls of a function |

All tools return JSON strings; the LLM reasons over the raw LSP output.

## Installation

Install clangd through your system package manager:

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

If clangd is not on `PATH`, set the `CLANGD_PATH` environment variable to the
full binary path (e.g. `/usr/lib/llvm-18/bin/clangd`).

## compile_commands.json

For accurate results, your project root needs a `compile_commands.json` file.
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

## Position conventions

All tools accept **1-based line numbers** and **0-based character offsets**,
matching `treesitter_get_symbols` and common editor conventions (VS Code,
vim). Outputs are converted from LSP's 0-based to 1-based lines.

## Output format

Results are JSON strings capped at **8 000 characters**. When a result exceeds
this limit:

- **List outputs** (symbols, locations, completions): a
  `{"truncated": true, "omitted_count": N}` object is appended.
- **Dict outputs** (rename, type/call hierarchy): the string is truncated at
  the limit and suffixed with `"... (truncated)"`.

## Cold start

The clangd subprocess starts lazily on the first tool call. The first
invocation may take 2-5 seconds (clangd parses your project and builds an
index). Subsequent calls are fast (sub-millisecond for symbol lookups).

## Limitations

- **Single workspace.** clangd is configured with `os.getcwd()` as the project
  root. Changing the working directory at runtime is not supported.
- **No auto-restart on compile_commands.json changes.** If you regenerate
  `compile_commands.json` while the agent is running, clangd does not pick up
  the changes. Restart the agent to reload.
- **Workspace symbols need a warm file.** `clangd_workspace_symbols` may return
  empty results until at least one file has been opened (via another tool that
  navigates to a file). This is a clangd behaviour — it only indexes files
  that have been opened or queried.
- **Cursor placement matters for definition.** `clangd_definition` returns the
  symbol the cursor is *on*, not the nearest enclosing token. Place the cursor
  directly on the symbol name.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CLANGD_PATH` | `clangd` | Path to the clangd binary |

## Troubleshooting

**Tool returns `"Error: clangd not found at '...'"`** — clangd is not installed
or not on `PATH`. Install it or set `CLANGD_PATH`.

**Tool returns `"Error: path ... is outside the project root"`** — the file is
outside `os.getcwd()`. Only files under the project root are accessible.

**Workspace symbols return empty `[]`** — no file has been opened yet. Call
another tool first (e.g. `clangd_document_symbols`) on a file in the project.
