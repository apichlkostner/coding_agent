# Task: Fix undefined signed overflow in `calc::checked_add`

The `calc::checked_add` function in `src/calculator.cpp` is supposed to return
the sum of two integers when the result is representable as an `int`, and
`std::nullopt` otherwise. The current implementation performs the addition
before checking the result, which invokes undefined behavior on signed
overflow. Find and fix the root cause.

## Requirements

- `calc::checked_add(int lhs, int rhs)` must return `std::optional<int>`
  containing `lhs + rhs` when the sum is representable as an `int`, and
  `std::nullopt` when it is not.
- The overflow check must not evaluate `lhs + rhs` when the result would
  overflow. Do not rely on detecting overflow after the addition.
- Do not change the public interface in `include/calculator.h`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes the build (`-Wall -Wextra -Werror -Wpedantic`),
  `clang-format`, `clang-tidy`, and the test binary.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise edge
cases such as `INT_MIN`, `INT_MAX`, zero, and mixed-sign operands. Passing the
visible tests alone is not sufficient.
