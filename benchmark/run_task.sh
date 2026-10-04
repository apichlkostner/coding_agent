#!/usr/bin/env bash
# Benchmark orchestrator.
#
# Runs a coding-agent task in a disposable copy of its repository, grades the
# result, and writes a JSON record. The reference solution and grader live
# outside the agent's working directory and are never visible to the agent.
#
# Isolation model
# ---------------
# The devcontainer is the isolation boundary: the agent runs inside it, so the
# host is protected by the container.  Within the devcontainer this script:
#
#   * copies the task repo to a fresh temp directory (the agent's CWD),
#   * runs the agent from a *copy* of the agent source, so the real source
#     tree cannot be modified by the agent,
#   * passes secrets via the environment from a dedicated benchmark .env
#     (benchmark/.env) when present, falling back to the repo .env.
#
# firejail is intentionally NOT used: its filesystem/network isolation is a
# no-op inside this devcontainer (user namespaces are blocked by the container
# seccomp profile), so relying on it would give a false sense of safety.
#
# Usage:
#   ./run_task.sh <task-id> [--keep]
#
# Options:
#   --keep   Keep the temporary working directory for inspection.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BENCH_DIR/.." && pwd)"
RESULTS_DIR="$BENCH_DIR/results"

TASK_ID=""
KEEP=0

for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) TASK_ID="$arg" ;;
  esac
done

if [[ -z "$TASK_ID" ]]; then
  echo "Usage: $0 <task-id> [--keep]" >&2
  echo "Available tasks:" >&2
  ls -1 "$BENCH_DIR/tasks" >&2
  exit 2
fi

TASK_DIR="$BENCH_DIR/tasks/$TASK_ID"
if [[ ! -d "$TASK_DIR" ]]; then
  echo "No such task: $TASK_ID" >&2
  exit 2
fi

for required in repo task.md grader/check.sh; do
  if [[ ! -e "$TASK_DIR/$required" ]]; then
    echo "Task $TASK_ID is missing $required" >&2
    exit 2
  fi
done

mkdir -p "$RESULTS_DIR"

WORK_DIR="$(mktemp -d "/tmp/bench-${TASK_ID}-XXXXXX")"
REPO_COPY="$WORK_DIR/repo"
AGENT_SRC="$WORK_DIR/agent_src"
AGENT_LOG="$WORK_DIR/agent.log"
GRADE_LOG="$WORK_DIR/grade.log"

cleanup() {
  if [[ "$KEEP" -eq 1 ]]; then
    echo "Kept working directory: $WORK_DIR"
  else
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

echo "==> Preparing disposable repo copy"
cp -a "$TASK_DIR/repo" "$REPO_COPY"

# Give the copy its own git history so the agent cannot see the solution.
if [[ ! -d "$REPO_COPY/.git" ]]; then
  git -C "$REPO_COPY" init -q
  git -C "$REPO_COPY" add -A
  git -C "$REPO_COPY" -c user.email=bench@local -c user.name=bench \
    commit -qm "Initial state"
fi

# Run the agent from a copy of its source so the real tree cannot be modified.
echo "==> Copying agent source"
cp -a "$REPO_ROOT/src" "$AGENT_SRC"

PROMPT="$(cat "$TASK_DIR/task.md")"

echo "==> Running agent on task: $TASK_ID"
START_TS="$(date +%s)"

# Load secrets into this process's environment. Prefer a dedicated benchmark
# .env (scoped key) over the main repo .env.
ENV_FILE="$BENCH_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  ENV_FILE="$REPO_ROOT/.env"
fi
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# Run the agent with the disposable repo as CWD and the copied source on the
# import path. PYTHONPATH points at the copy, so `import agent` resolves there.
# AGENT_CONFIG points at the real config, since the copied source has no config.
(
  cd "$REPO_COPY"
  PYTHONPATH="$AGENT_SRC" \
    AGENT_CONFIG="$REPO_ROOT/config/config.yaml" \
    "$REPO_ROOT/.venv/bin/python" -m agent --prompt "$PROMPT"
) >"$AGENT_LOG" 2>&1 || true

END_TS="$(date +%s)"
DURATION=$((END_TS - START_TS))

echo "==> Grading"
# Inject the grader into the copy, then run it. The agent never saw these files.
cp -a "$TASK_DIR/grader" "$REPO_COPY/grader"
GRADE_EXIT=0
( cd "$REPO_COPY" && bash grader/check.sh ) >"$GRADE_LOG" 2>&1 || GRADE_EXIT=$?

if [[ "$GRADE_EXIT" -eq 0 ]]; then
  PASSED=true
else
  PASSED=false
fi

# Persist the agent output and grade output alongside the JSON record.
cp "$AGENT_LOG" "$RESULTS_DIR/$TASK_ID.agent.log"
cp "$GRADE_LOG" "$RESULTS_DIR/$TASK_ID.grade.log"

# The structured JSON log is written relative to the agent's CWD (the repo
# copy). Preserve it before the temp directory is removed.
STRUCTURED_LOG="$REPO_COPY/logs/agent.log"
if [[ -f "$STRUCTURED_LOG" ]]; then
  cp "$STRUCTURED_LOG" "$RESULTS_DIR/$TASK_ID.jsonl"
fi

python3 - "$RESULTS_DIR/$TASK_ID.json" "$TASK_ID" "$PASSED" "$DURATION" \
  "$GRADE_EXIT" "$AGENT_LOG" "$GRADE_LOG" <<'PY'
import json
import sys
from pathlib import Path

out_path, task_id, passed, duration, grade_exit, agent_log, grade_log = sys.argv[1:8]

record = {
    "task": task_id,
    "passed": passed == "true",
    "duration_seconds": int(duration),
    "grade_exit_code": int(grade_exit),
    "agent_output": Path(agent_log).read_text(encoding="utf-8", errors="replace"),
    "grade_output": Path(grade_log).read_text(encoding="utf-8", errors="replace"),
}
Path(out_path).write_text(json.dumps(record, indent=2), encoding="utf-8")
PY

if [[ "$PASSED" == "true" ]]; then
  echo "RESULT: PASS ($TASK_ID, ${DURATION}s)"
else
  echo "RESULT: FAIL ($TASK_ID, ${DURATION}s, grade exit $GRADE_EXIT)"
  echo "--- grade output ---"
  cat "$GRADE_LOG"
fi

[[ "$PASSED" == "true" ]]
