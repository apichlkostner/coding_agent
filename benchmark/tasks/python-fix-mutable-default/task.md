# Task: Fix unintended state sharing in `append_label`

`labels.append_label` is documented to append `label` to a list of labels and
return that list. When the caller omits the `labels` argument, each call is
supposed to start from a fresh, empty list. Instead, calls that omit the
argument share state: labels appended by one call leak into later calls. Find
and fix the root cause.

## Requirements

- `append_label` keeps its public callable shape: a `label: str` parameter and
  an optional `labels: list[str]` parameter with a list default, returning
  `list[str]`. Argument names must not change.
- When `labels` is omitted, the call must start from a new empty list, append
  `label`, and return that new list. Two calls that omit `labels` must return
  independent lists.
- When `labels` is supplied explicitly, the function must append `label` to that
  same list and return that same object (identity preserved), including when the
  supplied list is empty.
- Positional and keyword invocation must both work.
- Do not change the public API in `src/labels/__init__.py`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes `ruff check`, `ruff format --check`, `pyright`, and
  `pytest`.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise repeated
omitted calls and explicit-list identity. Passing the visible tests alone is not
sufficient.
