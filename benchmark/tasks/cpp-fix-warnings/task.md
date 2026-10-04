# Task: Fix the out-of-bounds bug in `calc::sum`

The `calc::sum` function in `src/calculator.cpp` crashes or reads out of bounds
for some inputs. Find and fix the root cause.

## Requirements

- `calc::sum` must return the correct sum for any vector, including the empty
  vector.
- Do not change the public interface in `include/calculator.h`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes the build (`-Wall -Wextra -Werror -Wpedantic`),
  `clang-format`, `clang-tidy`, and the test binary.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise edge
cases. Passing the visible tests alone is not sufficient.
