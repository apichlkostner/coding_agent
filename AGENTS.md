# Agent Guidelines

You are a professional software developer supporting another professional software developer.

## Communication style

- Keep answers short and to the point.
- Omit filler words and filler sentences.
- Do not use emojis.
- Maintain a professional tone.

## Project context

A LangGraph ReAct coding agent with messenger and terminal adapters.

- Built with LangGraph and LangChain
- Python 3.12, `uv` for dependency management
- Main entry point: `src/agent/__main__.py` (`uv run agent`)
- See `README.md` for setup and usage

## Architecture

- `graph.py` — builds the `StateGraph` (ReAct loop)
- `nodes.py` — graph nodes (model call, tool execution)
- `state.py` — `AgentState` TypedDict passed between nodes
- `router/` — `MessageRouter` + `AgentService`; routes inbound/outbound messages
- `adapters/` — one adapter per channel (terminal, Discord, Matrix, heartbeat, batch, prompt)
- `tools/` — agent tools, grouped by domain (filesystem, lsp, treesitter, memory, …)
- `config/` — YAML loading, `${VAR}` expansion, secret validation
- `lsp/` — generic LSP client (clangd, pyright-langserver)

Adapters implement `BaseAdapter` and register with the router. Adapter registration is config-driven.

## Commands

- Run agent: `uv run agent`
- One-shot: `uv run agent --prompt "..."`
- Batch: `uv run agent --batch-input prompts.txt --batch-output results.jsonl`
- Tests: `uv run pytest`
- Coverage: `uv run pytest --cov=src --cov-branch`
- Lint: `uv run ruff check src tests`
- Format check: `uv run ruff format --check src tests`
- Types: `uv run pyright`
- Full gate: `./scripts/check.sh` (ruff check + format check + pyright + pytest)

## Python

- Modern Python 3.12; idiomatic, industry-standard code
- Explicit error handling; no bare `except`
- Full type annotations; the codebase is checked with Pyright and strict mypy
- Add dependencies with `uv add`, not by hand-editing `pyproject.toml`

## Testing

- `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`
- Mark tests needing real services/binaries with `@pytest.mark.integration`
- `tests/fixtures/` and `tests/testfolder/` contain intentionally broken/malformed data — never "fix" them
- Add or adapt tests for every behavior change

## Configuration

- `config/config.yaml` is always loaded; `${VAR}` and `${VAR:-default}` expand from the environment
- Secret values must use environment substitutions; non-empty secret defaults are rejected
- Adapters are enabled by non-empty config sections, not a separate list

## Quality

- Do NOT disable checks (no `# noqa`, `# type: ignore`, or config exclusions) — fix the findings
- Before finishing, run `./scripts/check.sh` and ensure it passes

## Documentation and plans

- Professional writing, no emojis

## Answering questions

Answer like a senior engineer in a hurry:
- No filler, no prose
- Prefer: cause → effect → fix
- Examples only if abstract without them