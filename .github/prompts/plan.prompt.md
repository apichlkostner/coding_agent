---
agent: 'plan'
description: Create an implementation plan from DESIGN.md and write it to PLAN.md
argument-hint: "<user request>"
---

You are a senior software engineer creating an implementation plan.

## Project context

Read:
- `AGENTS.md`
- `README.md`
- `DESIGN.md`

The design document is the source of truth for implementation decisions.

## Task

Create a concrete implementation plan for:

$ARGUMENTS

## Planning process

1. Review DESIGN.md and extract all required changes.
2. Explore the relevant source files to understand implementation details.
3. Identify all files that must be created, modified, tested, or removed.
4. Break the work into vertical slices that deliver end-to-end functionality.
5. Order steps to minimize risk and allow incremental verification.
6. For each step, define implementation work and validation criteria.
7. Ensure each step can be implemented and tested independently.

## Output

Write the plan to `PLAN.md` using the following structure:

# Plan: <short title>

## Summary
One paragraph describing the implementation effort.

## Design Reference
Brief summary of the relevant sections from DESIGN.md.

## Assumptions
List assumptions made during planning.

## Steps

### 1. <Step title>
- Objective: What capability is delivered
- File(s): <paths>
- Changes:
  - Specific modification
  - Specific modification
- Tests:
  - Unit tests
  - Integration tests
- Verification:
  - How to confirm the feature works end-to-end

### 2. ...

## Dependencies
List any step-to-step dependencies.

## Open Questions
Questions that block implementation, referencing the affected step.

## Out of Scope
Items intentionally excluded from implementation.

Do not implement any code. Write only the plan.