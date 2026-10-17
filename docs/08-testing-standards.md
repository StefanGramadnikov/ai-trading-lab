# 08 — Testing and code quality standards

**Status:** The user's requirement for tests on **every development cycle** is APPROVED. Tooling and commands become exact when M1 scaffolding is approved. **Date:** 2026-10-17.

## Definition of done for every code change

- [ ] Requirements and impacted interfaces reviewed.
- [ ] New feature includes targeted unit/integration tests and negative/boundary cases.
- [ ] Bug fix includes a regression test that would fail before the fix.
- [ ] Existing tests were updated if and only if approved behavior changed; tests were not weakened to make failures disappear.
- [ ] Format/lint/type checks run and pass.
- [ ] Entire required local test suite runs and passes; slow suites can have documented isolated staging gates but cannot be silently skipped.
- [ ] CI green on the exact commit intended for merge.
- [ ] No live trading endpoints or credentials used during tests.
- [ ] Relevant documentation, decision register (if needed) and progress log updated.
- [ ] PR/commit or Codex response includes actual commands, results, open risks and evidence.

If a tool cannot run, report `NOT RUN` with a reason. Never claim `PASS` based on expectation.

## Proposed tools (M1 validates versions)

`pytest`, `pytest-asyncio`, `pytest-cov`, `Ruff` (format + lint), `mypy` or `Pyright` (select and pin one), `pre-commit`, and GitHub Actions. For DB integration tests use disposable PostgreSQL fixtures/containers; API contract tests use recorded sanitized responses and schema snapshots. Avoid hitting paid live APIs in CI.

## Test pyramid

| Layer | Focus | Minimum examples |
| --- | --- | --- |
| Unit | Strategy math, currency precision, thresholds, schema validation | Boundary, None/NaN, stale data, rounding, currencies |
| Contract | External adapter schema and failure mapping | 200/429/5xx, malformed JSON, changed fields, rate limits |
| Integration | DB transactions, ledger, scheduler, risk/service boundaries | Simulate exchange timeout, duplicate event, restart recovery |
| End-to-end paper | Source fixture → proposal → risk → simulated order → ledger → report | Successful, rejected, partially filled and halted paths |
| Property/invariant | General financial truths | Assets conserved within model; no negative unrestricted cash; no duplicate fills |
| Fault injection | Operational degradation and concurrency | Kill/restart worker, disconnect DB, clock skew, partial batch ingestion |

## Mandatory financial invariants

- No trade without positive validated instrument identity, account and order intent.
- No automatic order if critical source is stale or untrusted.
- No unapproved increase in risk limit, negative available cash or unbounded exposure.
- Idempotent processing for all order/fill events; lost response must reconcile before retry.
- Consistent debits/credits, position quantities, reserved cash and fee recognition.
- No double-counted fees in P&L; settlement and open positions handled separately.
- Decimal unit correctness: EUR/USD, token quantities, percentages vs basis points.
- Identical replay input/config produces identical deterministic outputs (record nondeterministic AI responses separately).
- All runtime trading modes without separately approved live gate remain `PAPER_ONLY`.

## AI-specific testing

- Schema-valid and schema-invalid output, prompt injection, model timeout, rate limit, provider billing exceptions.
- Evidence/provenance consistency: model cannot cite an endpoint that wasn't called.
- Budget breaker is exercised, including retries and tool invocations.
- Multi-agent collaboration has step/token/time caps and cannot bypass deterministic risk engine.
- Provider model may be mocked for CI, but results must clearly distinguish fixtures from actual provider calls.

## Performance and coverage policy

- No arbitrary single percent proves safety. Establish minimum coverage targets at M1 after baselining the initial code; critical accounting/risk functions should have exhaustive edge-condition tests and invariants.
- As feasible, benchmark event-handling latency and memory footprint using archived/reproducible fixture data.
- API rates/quotas and financial calculations require regression fixtures.

## Commands and CI gate

M1 will define a single **developer quality command** (e.g., `make check` or `uv run ...`) that runs formatting check, lint, typing and all required tests; it must run locally and in GitHub Actions. Do **not** assume illustrative commands exist prior to M1.

Every Codex task should end with a concise checklist:

```text
Changed: ...
Tests added/updated: ...
Formatting: PASS/FAIL/NOT RUN (exact command)
Lint: PASS/FAIL/NOT RUN (exact command)
Type checks: PASS/FAIL/NOT RUN (exact command)
Unit/integration/e2e: PASS/FAIL/NOT RUN (commands + counts)
Known limitations: ...
Decision/progress docs: ...
```
