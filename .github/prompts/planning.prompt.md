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

Convert the approved design into an implementation plan.

## Process

1. Review DESIGN.md.
2. Explore relevant code.
3. Identify files to change.
4. Break implementation into vertical slices.
5. Define tests and verification for each slice.
6. Surface implementation risks.

If DESIGN.md contains unresolved critical questions:

Stop and report them instead of creating PLAN.md.

## Output

Write PLAN.md.

Do not implement code.
Do not change the design.
