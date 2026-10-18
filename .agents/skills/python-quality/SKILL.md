---
name: python-quality
description: Implement or verify AI Trading Lab Python runtime, tests, persistence, adapters and build configuration using the project's deterministic quality gate. Use for substantive Python changes, not generic document edits or trading execution.
---

Use `AGENTS.md` and `docs/08-testing-standards.md` for required checks and
`docs/17-development-workflow.md` for repair, review and ship boundaries.
This is project guidance, not an official Python skill or permission to ship.

Identify the contract, acceptance/failure cases and test boundary before coding.
Keep concrete interface types; validate external payloads before trusting them.
Comments explain domain assumptions and reasons, not every trivial function.

Check the risks relevant to the change:

- Money/quantities: Decimal or fixed-point through parsing, calculation and SQL;
  units/currencies explicit; missing values do not become zero.
- Time/data: timezone-aware UTC, event time separate from receipt time, missing
  source times explicit; no future leakage or silent stale-data fallback.
- Identity/provenance: native venue/instrument/outcome keys, immutable evidence
  and schema versions; descriptions/AI output never grant execution authority.
- I/O/resources: injected clocks/transports, bounded timeouts/retries/concurrency,
  rate-limit handling, payload limits, transaction boundaries and cleanup.
- Persistence: isolated real PostgreSQL tests for SQL/constraints/rollback and
  duplicate/replay behavior where relevant. Add a migration rather than editing
  an applied/shared migration. Do not build future-milestone domain services.

Write behavioral positive, boundary, failure and regression cases before or
alongside implementation. Contract tests use sanitized fixtures and exercise
malformed/partial data and 429/5xx/timeouts. Isolate clock, environment and test
state; avoid order dependencies and arbitrary sleeps. Separate public-data smoke
evidence from deterministic CI; never call execution endpoints.

Run focused checks during iteration, then the full required `make check`.
Diagnose/fix within scope using the bounded repair loop; never weaken tests or
infer a pass. Independently review substantive changes and report commands,
results and untested aspects. Wait for `/ship` before commit/push/PR actions.
