#!/usr/bin/env bash
# Run every benchmark task and print a summary table.
#
# Usage:
#   ./run_all.sh [--keep]
set -uo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$BENCH_DIR/results"
mkdir -p "$RESULTS_DIR"

PASS=0
FAIL=0
declare -a ROWS=()

for task_dir in "$BENCH_DIR"/tasks/*/; do
  task_id="$(basename "$task_dir")"
  if "$BENCH_DIR/run_task.sh" "$task_id" "$@" && "$BENCH_DIR/run_grader.sh" "$task_id" "$@"; then
    status="PASS"
    PASS=$((PASS + 1))
  else
    status="FAIL"
    FAIL=$((FAIL + 1))
  fi
  ROWS+=("$task_id|$status")
done

echo
echo "==================== Benchmark summary ===================="
printf "%-28s %s\n" "TASK" "RESULT"
printf "%-28s %s\n" "----" "------"
for row in "${ROWS[@]}"; do
  printf "%-28s %s\n" "${row%%|*}" "${row##*|}"
done
echo "-----------------------------------------------------------"
printf "Total: %d  Passed: %d  Failed: %d\n" "$((PASS + FAIL))" "$PASS" "$FAIL"

[[ "$FAIL" -eq 0 ]]
