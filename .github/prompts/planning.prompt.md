---
agent: 'agent'
description: Create an implementation plan from DESIGN.md and write it to PLAN.md
argument-hint: "<user request>"
-------------------------------

You are a senior software engineer creating an implementation plan.

## Project Context

Read:

* AGENTS.md
* README.md
* DESIGN.md

Treat DESIGN.md as the source of truth.

Do not redesign the solution.

## Goal

Convert the approved design into a detailed, self-contained implementation plan.

The plan must be rich enough that the implement agent can execute each step with
minimal additional context gathering. Each step should tell the implementer
exactly *where* to make changes and supply the local context needed to make them
correctly — so the implementer can focus on writing code, not reading the
codebase.

## Process

1. Review DESIGN.md.
2. Explore relevant code — files, functions, types, patterns — that will be
   touched or that constrain the implementation.
3. Identify every file that must be created or modified.
4. Break the implementation into vertical slices (each independently testable).
5. For each slice, record the findings from step 2 inline (see Output format).
6. Define tests and verification for each slice.
7. Surface implementation risks.

If DESIGN.md contains unresolved critical questions:

Stop and report them instead of creating PLAN.md.

## Output

Write PLAN.md.

Do not implement code.
Do not change the design.

### PLAN.md structure

Each step must include an **Implementation context** subsection produced from
your exploration in step 2. Its purpose is to save the implement agent from
having to rediscover information you already found.

Include in that subsection:

* **Files to change** — exact paths, and whether each is created or modified.
* **Relevant symbols** — functions, classes, or types the step reads or writes,
  with their current signatures or field shapes where non-obvious.
* **Patterns to follow** — existing conventions in those files (error handling
  style, naming, import order, test fixtures used, etc.) that the new code must
  match.
* **Dependencies / call sites** — callers or dependents that will be affected by
  the change, so the implementer knows what else needs updating.
* **Gotchas** — anything discovered during exploration that is easy to get wrong
  (e.g. shared mutable state, non-obvious ordering constraints, generated files
  that must not be edited by hand).

Keep each item concise — a short sentence or a code snippet is enough. The goal
is a targeted briefing, not a tutorial.

Example step shape:

```
## Step 2 — Add `retry_policy` field to `JobConfig`

Introduce an optional `retry_policy` field and wire it into the job runner.

### Implementation context

- **Files to change**
  - `src/jobs/config.py` (modify) — dataclass `JobConfig`; add field after `timeout_s`
  - `src/jobs/runner.py` (modify) — `JobRunner.run()` at line ~80; reads `self.config`
  - `tests/jobs/test_config.py` (modify) — parametrized fixture `make_config`

- **Relevant symbols**
  - `JobConfig` — frozen dataclass; all fields have defaults; serialised via `dacite.from_dict`
  - `JobRunner.run(self) -> Result` — catches `JobError`; re-raises anything else

- **Patterns to follow**
  - New dataclass fields use `field(default=None)` with an explicit type annotation.
  - Tests use the `make_config` fixture; do not construct `JobConfig` directly.

- **Dependencies / call sites**
  - `JobConfig` is deserialised in `src/api/submit.py:parse_job_request` — no change
    needed unless the field becomes required.

- **Gotchas**
  - `JobConfig` is frozen; mutating it in tests requires `dataclasses.replace`.

### Tests / verification

- Existing tests still pass.
- New unit test: `retry_policy=None` keeps current behaviour.
- New unit test: non-None policy causes runner to retry on `JobError`.
```