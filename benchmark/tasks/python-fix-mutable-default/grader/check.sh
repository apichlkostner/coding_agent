#!/usr/bin/env bash
# Authoritative grader for the Python task.
# Runs the public quality gate, then injects and runs hidden edge-case tests.
set -euo pipefail

echo "==> Public quality gate"
./check.sh

echo "==> Hidden tests"
uv run pytest -q grader/test_hidden.py

echo "GRADER: PASS"
