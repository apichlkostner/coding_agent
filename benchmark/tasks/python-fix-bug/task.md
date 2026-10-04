# Task: Fix the tie-breaking bug in `most_common_word`

`textstats.most_common_word` is documented to break ties by first appearance in
the text, but it does not. Find and fix the root cause.

## Requirements

- `most_common_word` must return the word that appears most often.
- When two or more words share the highest count, return the one that appears
  first in the text.
- Return an empty string for empty input.
- Do not change the public API in `src/textstats/__init__.py`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes `ruff check`, `ruff format --check`, `pyright`, and
  `pytest`.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise tie
cases. Passing the visible tests alone is not sufficient.
