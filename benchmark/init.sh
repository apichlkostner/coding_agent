#!/usr/bin/env bash
# Shared setup for the benchmark harness. Sourced by run_task.sh and
# run_grader.sh; not meant to be executed directly.
set -euo pipefail

echo "Initialize benchmark task..."

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

mkdir -p "$RESULTS_DIR"

# Per-task state file so concurrent runs (e.g. run_all.sh) do not collide.
COM_ENV_FILE="/tmp/BENCHMARK_ENV.$TASK_ID"
if [[ ! -f "$COM_ENV_FILE" ]]; then
    echo "No COM_ENV_FILE file exists"
    WORK_DIR="$(mktemp -d "/tmp/bench-${TASK_ID}-XXXXXX")"
    echo "Create new working directory $WORK_DIR"
    echo "WORK_DIR=$WORK_DIR" > "$COM_ENV_FILE"
    echo "Written WORKDIR to file $COM_ENV_FILE"
else
    echo "Using existing COM_ENV_FILE file $COM_ENV_FILE"
    source "$COM_ENV_FILE"
    echo "Using existing working directory $WORK_DIR"
fi

# DURATION is written by run_task.sh and read by run_grader.sh. Default it so
# run_grader.sh does not abort under `set -u` when run standalone.
DURATION="${DURATION:-0}"

REPO_COPY="$WORK_DIR/repo"
AGENT_SRC="$WORK_DIR/agent_src"
AGENT_LOG="$WORK_DIR/agent.log"
GRADE_LOG="$WORK_DIR/grade.log"

echo "Finished init"