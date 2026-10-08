# Coding-agent benchmark

A small harness for testing the agent on real coding tasks with automated,
behavior-based grading.

## Design

Each task is a self-contained directory:

```
tasks/<task-id>/
  repo/          # starting state; the agent's working directory (own git repo)
  task.md        # the prompt handed to the agent
  solution/      # reference solution (host-only, never visible to the agent)
  grader/
    check.sh     # authoritative checks, run after the agent exits
    ...          # hidden tests injected at grade time
```

The agent runs with `repo/` as its working directory. `solution/` and `grader/`
are siblings of `repo/`, so they are outside the agent's filesystem sandbox and
never appear in its git history. The grader is copied into the working copy only
after the agent has stopped.

## Isolation

Three possible grades of isolation are supported:

- Just run everything on the host without isolation
    - No protection against agentic misbehavior
- Use the devcontainer for isolation
    - Host is protected against the agents behavior
    - Devcontainer and task solution are not protected
- Use an extra container to run the benchmark
    - Host and devcontainer are protected against the agents behavior
    - Solutions are protected
    - Git history is protected

### No extra isolation

To run it without extra container, use `run_task.sh` on the host or in the devcontainer.

### Stronger isolation: run in a dedicated container

For stronger isolation, run tasks in a dedicated container instead of the
devcontainer. Docker runs on the **host**; the devcontainer drives the container
over SSH. See [`docker/README.md`](docker/README.md).

```bash
# On the host: build and start the container.
./benchmark/docker/run_container.sh

# In the devcontainer: run a task inside the container.
./benchmark/run_task_remote.sh python-fix-bug
```

### Threat model

| Threat | Mitigated by |
|---|---|
| Agent damages the host | the devcontainer boundary |
| Agent damages the agent source | agent runs from a copied source tree |
| Agent damages the task repo | disposable copy per run |
| Agent reads secrets | scoped `benchmark/.env` (see below) |

When running via `run_task_remote.sh`, the container replaces the devcontainer
as the boundary, additionally protecting the devcontainer itself.

The agent's `bash` tool is unrestricted, so it can read any file the devcontainer
user can read — including the repo `.env` if you use it. Use a dedicated
`benchmark/.env` with a **scoped, low-privilege API key** to limit the blast
radius. The harness prefers `benchmark/.env` when it exists.

## Grading

A task passes only if `grader/check.sh` exits 0. Grading is behavior-based:

1. **Build** — compiles cleanly with warnings-as-errors
2. **Lint/format** — `clang-format`/`clang-tidy` (C++), `ruff` (Python)
3. **Public tests** — the tests shipped in `repo/`
4. **Hidden tests** — injected from `grader/` at grade time

The agent is told the acceptance criteria in `task.md` and in the repo's
`AGENTS.md`, which the agent loads into its system prompt automatically.

## Usage

```bash
# Run one task
./run_task.sh cpp-fix-warnings

# Keep the temporary working directory for inspection
./run_task.sh cpp-fix-warnings --keep

# Run all tasks and print a summary table
./run_all.sh
```

Results are written to `results/<task-id>.json` (plus `.agent.log` and
`.grade.log`).

### Result artifacts

| File | Contents |
|---|---|
| `<task-id>.json` | Pass/fail, duration, grade exit code, captured output |
| `<task-id>.agent.log` | Agent stdout — the human-readable `[agent] →` / `[tools] ←` stream |
| `<task-id>.jsonl` | Structured JSON log (one JSON object per line) |
| `<task-id>.grade.log` | Grader output |

The `.agent.log` is the terminal adapter's rendering of the run. The `.jsonl`
is the structured log with `message_id`, `thread_id`, and `event` fields, useful
for programmatic analysis.

### Secrets

Create `benchmark/.env` with a scoped API key to limit what the agent can read:

```ini
API_KEY_LITELLM=<scoped-key>
```

When `benchmark/.env` is absent the harness falls back to the repo `.env`.

## Adding a task

1. Create `tasks/<task-id>/repo/` with the starting state, an `AGENTS.md`
   describing the quality gate, and a `check.sh` the agent can run.
2. Write `task.md` stating the goal and the acceptance criteria.
3. Put a reference solution in `solution/`.
4. Put the authoritative checks and hidden tests in `grader/`.

## Requirements

- `g++`, `clang-format`, `clang-tidy` for C++ tasks
- `uv` for Python tasks
