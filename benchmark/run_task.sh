#!/usr/bin/env bash
# Benchmark runner.
#
# Runs a coding-agent task in a disposable copy of its repository. Grading is
# a separate step (run_grader.sh) so the grader can be injected only after the
# agent has stopped. The reference solution and grader live outside the agent's
# working directory and are never visible to the agent.
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

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/init.sh"

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
#
# BENCH_PYTHON and BENCH_CONFIG are overridable so the harness can run inside a
# container that has no repo .venv (see run_task_remote.sh).
BENCH_PYTHON="${BENCH_PYTHON:-$REPO_ROOT/.venv/bin/python}"
BENCH_CONFIG="${BENCH_CONFIG:-$REPO_ROOT/config/config.yaml}"
(
  cd "$REPO_COPY"
  PYTHONPATH="$AGENT_SRC" \
    AGENT_CONFIG="$BENCH_CONFIG" \
    "$BENCH_PYTHON" -m agent --prompt "$PROMPT"
) >"$AGENT_LOG" 2>&1 || true

END_TS="$(date +%s)"
DURATION=$((END_TS - START_TS))

echo "DURATION=$DURATION" >> "$COM_ENV_FILE"
