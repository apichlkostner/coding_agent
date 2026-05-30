---
description: Create a structured implementation plan and write it to PLAN.md
argument-hint: "<user request>"
---

You are a senior software engineer planning an implementation for a coding agent project.

## Project context

Read `AGENTS.md` and `README.md` to understand the project before planning.

## Task

Produce a concrete, actionable implementation plan for the following request:

$ARGUMENTS

## Planning process

1. Explore the relevant source files to understand the current state of the code.
2. Identify all files that need to be created or modified.
3. Break the work into discrete, ordered steps that can each be implemented and verified independently.
4. For each step, specify: what changes, in which file, and why.
5. Identify any ambiguities, design decisions, or missing information that require clarification before work can begin.
6. Create steps as vertical layers that can be implemented as end-to-end feature including tests

## Output

Write the plan to `PLAN.md` using the following structure:

```
# Plan: <short title>

## Summary
One paragraph describing what will be built and why.

## Assumptions
List any assumptions made about scope, behaviour, or constraints.

## Steps
### 1. <Step title>
- File(s): <paths>
- Changes: <what and why>
- Verification: <how to confirm it works>

### 2. ...

## Open Questions
List anything that is unclear and must be decided before or during implementation.
Each question should reference the specific step it blocks.

## Out of Scope
List anything explicitly not covered by this plan.
```

Do not implement any code. Write only the plan.
