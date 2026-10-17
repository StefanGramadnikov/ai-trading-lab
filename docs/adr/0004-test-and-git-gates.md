# ADR-0004 — Automated tests and private Git from first implementation cycle

- **Status:** APPROVED
- **Date:** 2026-10-17

## Context

The user requires old tests to be adjusted when legitimate behavior changes, new tests for new code and passing tests on **every development cycle**. Git versioning is mandatory from the outset.

## Decision

Initialize/reuse private Git before application code. Each change has relevant tests and green formatting, lint, static typing and required automated tests; GitHub Actions repeats checks on changes. Track source versions, decisions, progress and exact test evidence. Never weaken tests to conceal defects.

## Consequences

- More deliberate change cycles, but reduced risk of regressions in accounting, strategy evaluation and execution.
- A failed/unrun test means task incomplete or explicitly blocked, not done.
- The first commit should capture the documentation baseline, once reviewed.
