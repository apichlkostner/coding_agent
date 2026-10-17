# Task Repository Instructions

This is a small Python project managed with `uv`. Your change is accepted only
if it passes the quality gate defined in `check.sh`.

## Quality gate

Run `./check.sh` before you finish. It must exit 0. The gate runs:

1. **Lint** — `uv run ruff check .`
2. **Format** — `uv run ruff format --check .`
3. **Types** — `uv run pyright`
4. **Tests** — `uv run pytest`

## Rules

- Do not modify `check.sh` or the files under `tests/`.
- Do not add `# noqa`, `# type: ignore`, or config exclusions, except for the
  existing `B006` per-file ignore for `src/labels/labels.py`, which is required
  by the fixed public signature and must be left in place.
- Fix the root cause, not the symptom.
- Keep the public API in `src/labels/__init__.py` stable unless the task says
  otherwise.
