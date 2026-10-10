#!/usr/bin/env bash
# Run a benchmark task inside the remote benchmark container over SSH.
#
# RUN THIS INSIDE THE DEVCONTAINER. The container must already be running on the
# host (see benchmark/docker/run_container.sh).
#
# This script:
#   1. copies the harness, the task directory, the agent source, and the config
#      into the container,
#   2. syncs the agent's Python environment inside the container with uv,
#   3. runs benchmark/run_task.sh inside the container over SSH,
#   4. pulls the results back into benchmark/results/.
#
# Usage:
#   ./run_task_remote.sh <task-id> [--keep] [--host <host>] [--port <port>]
#
# Options:
#   --keep        Keep the remote working directory for inspection.
#   --host <h>    SSH host (default: host.docker.internal inside a container,
#                 otherwise localhost).
#   --port <n>    SSH port (default: 2222).
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BENCH_DIR/.." && pwd)"
RESULTS_DIR="$BENCH_DIR/results"

TASK_ID=""
KEEP=0
HOST="localhost"
PORT=2222

# Default host: inside a container (e.g. the devcontainer), `localhost` is not
# the host's `localhost`, so the published port is only reachable via the
# docker bridge gateway. Detect that case and default to host.docker.internal.
if [[ -f /.dockerenv ]] || grep -qs 'docker\|containerd\|kubepods' /proc/1/cgroup; then
  HOST="host.docker.internal"
else
  HOST="localhost"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *) TASK_ID="$1"; shift ;;
  esac
done

if [[ -z "$TASK_ID" ]]; then
  echo "Usage: $0 <task-id> [--keep] [--host <host>] [--port <port>]" >&2
  echo "Available tasks:" >&2
  ls -1 "$BENCH_DIR/tasks" >&2
  exit 2
fi

TASK_DIR="$BENCH_DIR/tasks/$TASK_ID"
if [[ ! -d "$TASK_DIR" ]]; then
  echo "No such task: $TASK_ID" >&2
  exit 2
fi

KEY="$BENCH_DIR/docker/id_ed25519"
if [[ ! -f "$KEY" ]]; then
  echo "SSH key not found: $KEY" >&2
  echo "Run benchmark/docker/run_container.sh on the host first." >&2
  exit 2
fi

SSH_OPTS=(-i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
# scp uses -P for port, not -p (which means "preserve timestamps").
SCP_OPTS=(-i "$KEY" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
SSH_TARGET="bench@$HOST"
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

# Remote layout: a fresh directory per run under the bench user's home.
REMOTE_ROOT="/home/bench/bench-run"
REMOTE_BENCH="$REMOTE_ROOT/benchmark"

echo "==> Checking container connectivity"
if ! ssh "${SSH_OPTS[@]}" "$SSH_TARGET" true; then
  echo "Cannot reach $SSH_TARGET on port $PORT." >&2
  echo "Is the container running? Start it on the host with run_container.sh." >&2
  exit 2
fi

echo "==> Preparing remote workspace"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "rm -rf '$REMOTE_ROOT' && mkdir -p '$REMOTE_BENCH/tasks'"

# Copy the harness scripts and the task directory.
echo "==> Copying harness and task"
scp "${SCP_OPTS[@]}" \
  "$BENCH_DIR/init.sh" \
  "$BENCH_DIR/run_grader.sh" \
  "$BENCH_DIR/run_task.sh" \
  "$BENCH_DIR/run_all.sh" \
  "$SSH_TARGET:$REMOTE_BENCH/"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "chmod +x '$REMOTE_BENCH/run_grader.sh' '$REMOTE_BENCH/run_task.sh' '$REMOTE_BENCH/run_all.sh'"

rsync -az -e "$RSYNC_SSH" "$TASK_DIR" --exclude "*/solution" --exclude "grader" "$SSH_TARGET:$REMOTE_BENCH/tasks/"

# Copy the agent source, config, and project metadata. The container has no
# checkout of the repo, so everything the agent needs is shipped explicitly.
echo "==> Copying agent source, config, and project metadata"
rsync -az -e "$RSYNC_SSH" \
  "$REPO_ROOT/src" \
  "$REPO_ROOT/config" \
  "$REPO_ROOT/pyproject.toml" \
  "$SSH_TARGET:$REMOTE_ROOT/"

# uv.lock is optional (gitignored); ship it when present for reproducible syncs.
if [[ -f "$REPO_ROOT/uv.lock" ]]; then
  rsync -az -e "$RSYNC_SSH" "$REPO_ROOT/uv.lock" "$SSH_TARGET:$REMOTE_ROOT/"
fi

# ---------------------------------------------------------------------------
# Sync the agent's Python environment inside the container.
# ---------------------------------------------------------------------------
echo "==> Syncing Python environment in the container"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
  "cd '$REMOTE_ROOT' && uv sync --quiet"

# ---------------------------------------------------------------------------
# Run the task inside the container.
# ---------------------------------------------------------------------------
echo "==> Running task '$TASK_ID' in the container"
REMOTE_KEEP=""
[[ "$KEEP" -eq 1 ]] && REMOTE_KEEP="--keep"

# run_task.sh resolves REPO_ROOT as the parent of benchmark/, so src/, config/,
# and .venv/ must sit at REMOTE_ROOT. BENCH_PYTHON/BENCH_CONFIG point the agent
# at the container's environment and config.
# Run the task inside the container. A failing grade is a valid outcome, not a
# harness error — don't let its nonzero exit abort before results are pulled.
RUN_EXIT=0
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
  "cd '$REMOTE_ROOT' && \
   BENCH_PYTHON='$REMOTE_ROOT/.venv/bin/python' \
   BENCH_CONFIG='$REMOTE_ROOT/config/config.yaml' \
   '$REMOTE_BENCH/run_task.sh' '$TASK_ID' $REMOTE_KEEP" || RUN_EXIT=$?

echo "==> Task run finished, starting grading"

# Inject the grader only now, after the agent has stopped, so an agent that
# searches the home directory cannot find it.
rsync -az -e "$RSYNC_SSH" "$TASK_DIR/grader" "$SSH_TARGET:$REMOTE_BENCH/tasks/$TASK_ID/"

GRADE_EXIT=0
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
  "cd '$REMOTE_ROOT' && \
   '$REMOTE_BENCH/run_grader.sh' '$TASK_ID' $REMOTE_KEEP" || GRADE_EXIT=$?

# ---------------------------------------------------------------------------
# Pull results back.
# ---------------------------------------------------------------------------
echo "==> Pulling results"
mkdir -p "$RESULTS_DIR"
rsync -az -e "$RSYNC_SSH" \
  "$SSH_TARGET:$REMOTE_BENCH/results/" "$RESULTS_DIR/"

echo "==> Done. Results in $RESULTS_DIR"

# Propagate failure from either phase as the script's exit code.
if [[ "$RUN_EXIT" -ne 0 ]]; then
  exit "$RUN_EXIT"
fi
exit "$GRADE_EXIT"
