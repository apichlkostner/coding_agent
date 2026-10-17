# Task: Implement `merge_intervals`

`intervals.merge_intervals` is documented to normalize a collection of integer
intervals into a sorted list of disjoint intervals. The starter implementation
is incomplete and does not satisfy the documented contract. Implement the
function so it meets every requirement below.

## Requirements

- Signature: `merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]`.
- Each input interval is an inclusive `(start, end)` pair with `start <= end`.
- Return a new list of intervals sorted by ascending `start`.
- Merge intervals that overlap **or touch**: if the next interval's `start` is
  less than or equal to the current interval's `end` (`next_start <= current_end`),
  they are combined into a single interval spanning both.
- Intervals that are strictly separated (`next_start > current_end`) remain
  distinct.
- Duplicate and nested intervals must collapse into the covering interval.
- Empty input returns an empty list.
- A single interval is returned unchanged (as a one-element list).
- Negative coordinates are supported.
- Any interval with `start > end` must raise `ValueError`.
- The input sequence must not be mutated.
- Do not change the public API in `src/intervals/__init__.py`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes `ruff check`, `ruff format --check`, `pyright`, and
  `pytest`.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise empty
input, sorted and unsorted input, duplicates, nested intervals, touching
endpoints, negative coordinates, single intervals, invalid ranges, and
non-mutation. Passing the visible tests alone is not sufficient.
