#!/usr/bin/env bash
# Grade a benchmark task that has already been run by run_task.sh.
#
# The grader is injected into the agent's repo copy only here, after the agent
# has stopped, so the agent never sees the hidden tests.
set -euo pipefail

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/init.sh"

for required in task.md grader/check.sh; do
  if [[ ! -e "$TASK_DIR/$required" ]]; then
    echo "Task $TASK_ID is missing $required" >&2
    exit 2
  fi
done

if [[ ! -d "$REPO_COPY" ]]; then
  echo "No repo copy at $REPO_COPY; run run_task.sh first." >&2
  exit 2
fi

cleanup() {
  if [[ "$KEEP" -eq 1 ]]; then
    echo "Kept working directory: $WORK_DIR"
  else
    rm -rf "$WORK_DIR"
  fi
  rm -f "$COM_ENV_FILE"
}
trap cleanup EXIT

echo "==> Grading"
# Inject the grader into the copy, then run it. The agent never saw these files.
cp -a "$TASK_DIR/grader" "$REPO_COPY/grader"
GRADE_EXIT=0
( cd "$REPO_COPY" && bash grader/check.sh ) >"$GRADE_LOG" 2>&1 || GRADE_EXIT=$?

# Remove the injected grader so a kept working directory stays a faithful
# post-agent snapshot.
rm -rf "$REPO_COPY/grader"

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