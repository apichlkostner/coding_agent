#!/usr/bin/env bash
set -euo pipefail

echo "==> ruff check"
uv run ruff check src tests

echo "==> ruff format --check"
uv run ruff format --check src tests

echo "==> pyright"
uv run pyright

echo "==> pytest"
uv run pytest --cov=src --cov-branch

echo "All checks passed."