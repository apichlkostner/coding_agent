# Task: Implement `dependency_order`

`dependency_order.order.dependency_order` is documented to compute a
deterministic topological ordering of a set of tasks from their prerequisites.
The starter implementation is incomplete and does not satisfy the documented
contract. Implement the function so it meets every requirement below.

## Requirements

- Signature:
  `dependency_order(dependencies: Mapping[str, Iterable[str]]) -> list[str]`.
- Each mapping key is a task name; its value is an iterable of the task names
  that must run **before** it (its prerequisites).
- The result is a list of every task name such that each task appears after all
  of its prerequisites.
- Names that appear only as prerequisites (never as a key) are still tasks and
  must appear in the result.
- Duplicate prerequisite entries have no additional effect: a repeated edge is
  treated as a single edge.
- The ordering is deterministic: at each step, among all tasks whose
  prerequisites have already been emitted, choose the lexicographically
  smallest name. This yields the unique lexicographically smallest valid
  topological order.
- An empty mapping returns an empty list.
- A task with no prerequisites may appear anywhere that respects the ordering,
  subject to the deterministic tie-break above.
- A cycle (including a self-cycle where a task lists itself as a prerequisite)
  must raise `ValueError`.
- The input mapping and its iterables must not be mutated.
- Do not change the public API in `src/dependency_order/__init__.py`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes `ruff check`, `ruff format --check`, `pyright`, and
  `pytest`.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise empty
graphs, disconnected nodes, chains, branching prerequisites, duplicate edges,
prerequisite-only nodes, deterministic ties, self-cycles, and multi-node
cycles. Passing the visible tests alone is not sufficient.
