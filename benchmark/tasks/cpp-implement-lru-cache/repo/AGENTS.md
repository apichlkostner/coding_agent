# Task Repository Instructions

This is a small C++ project. Your change is accepted only if it passes the
quality gate defined in `check.sh`.

## Quality gate

Run `./check.sh` before you finish. It must exit 0. The gate runs:

1. **Build** — `g++ -std=c++17 -Wall -Wextra -Werror -Wpedantic`
2. **Format** — `clang-format --dry-run --Werror` on all sources
3. **Static analysis** — `clang-tidy` on all sources
4. **Tests** — the project test binary

## Rules

- Do not modify `check.sh` or the test files.
- Do not weaken compiler flags or add suppressions (`#pragma`, `// NOLINT`).
- Fix the root cause, not the symptom.
- Keep the public interface in `include/` stable unless the task says otherwise.
