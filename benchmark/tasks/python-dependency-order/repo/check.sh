#!/usr/bin/env bash
# Quality gate for the Python task. Must exit 0 for the change to be accepted.
set -euo pipefail

echo "==> Sync environment"
uv sync --quiet

echo "==> Lint"
uv run ruff check .

echo "==> Format check"
uv run ruff format --check .

echo "==> Types"
uv run pyright

echo "==> Tests"
uv run pytest -q

echo "All checks passed"
