# 08 — Testing and code quality standards

**Status:** The user's requirement for tests on **every development cycle** is APPROVED. M1 tooling and commands below were approved on 2026-10-17. **Date:** 2026-10-17.

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

## M1 tools

`pytest`, `pytest-asyncio`, `pytest-cov`, `Ruff` (format + lint), `mypy` (strict mode with Pydantic plugin), `pre-commit`, and GitHub Actions. For DB integration tests use disposable PostgreSQL fixtures/containers; API contract tests use recorded sanitized responses and schema snapshots. Avoid hitting paid live APIs in CI.

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

`make check` is the M1 developer quality command; see the executable gate below.

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

## M1 executable quality gate — 2026-10-17

Python 3.12.13; uv 0.11.7; exact dependency versions in `uv.lock`.
Install with `uv sync --locked`. `make check` runs, in order:

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing
```

Export `ATL_TEST_DATABASE_URL` pointing to an isolated PostgreSQL database named
`ai_trading_lab_test` before running. Missing configuration fails the full suite;
wrong database names are rejected before migrations. The session fixture exercises
upgrade → downgrade → upgrade, then verifies readiness and transaction rollback.
Tests must not run concurrently against this same database. Application config
variables are isolated in tests; the test URL is retained explicitly.

Unit tests: `uv run --locked pytest tests/unit`. This alone is not the full gate.
Configuration tests cover defaults, invalid mode/timeout/URL, `.env` precedence,
and secret redaction. Health tests cover success/unavailable responses, no database
call for liveness, probe deadlines, error handling and engine cleanup. Integration
also verifies a real refused connection leaves liveness available and readiness 503.

M1 baseline is 100% statement/branch coverage for the small application package.
The 90% minimum guards against untested additions; it does not prove trading safety.
Domain financial invariants will require dedicated tests when those domains exist.
Migrations are checked by mypy and real PostgreSQL round trips, outside this coverage
percentage. No trading e2e tests exist because trading implementation is out of scope.

Pre-commit runs Ruff and mypy using the locked environment; install via
`uv run --locked pre-commit install`. CI repeats `make check` with a dedicated
PostgreSQL service, validates Compose, builds/starts the production image, runs its migration and probes both health endpoints. Hosted CI
must pass before merge; local success is not evidence of a hosted run.

## Review, repair and ship gate — 2026-10-18

Follow [the development workflow](17-development-workflow.md). Relevant integration
and contract tests run during development of those boundaries; QA starts with
acceptance cases. Focused or documentation checks do not replace `make check` before the change
is declared ready.

Independent review is required for substantive runtime/test logic, migrations,
dependencies, CI, permissions and workflow changes. It evaluates the actual full
change set and reports findings separately from automated test evidence. A review
PASS is not a test PASS; missing review evidence is INCOMPLETE.

Repair failed checks and confirmed findings within approved scope, with regression
coverage as appropriate. Rerun affected checks and the full required gate; re-review
material fixes. Stop after three automatic repair rounds per task or ship invocation
and ask for direction. Do not weaken tests or policies to achieve a pass.

The user invokes `/ship` for staging, commit, push and PR creation/update. After
material post-review changes, earlier review/check evidence is stale for those
changes. Hosted CI must pass on the actual final head before merging. No unattended
monitor, security scanner, frontend framework or dependency change is introduced
by this workflow-only update.


## M2 data verification — 2026-10-18

The same full `make check` gate includes four source contract suites and real
PostgreSQL market-data tests. Contract payloads are **synthetic fixtures**, not
claimed provider recordings: test names and assertions exercise native identity,
units, schema drift/malformed bodies, 429/5xx/decode failures, request budgets,
replay ordering and source-time quality. Public smoke evidence is separate in
[the source register](16-source-register.md); it never runs in deterministic CI.

Database tests exercise migration upgrade → downgrade → upgrade once per session,
exact NUMERIC/Decimal and JSON round trips, immutable capture/snapshot collisions,
metadata versioning, distinct polls versus duplicate delivery, atomic rollback,
source failure persistence through the CLI and offline replay. Tests refuse the
wrong database name. Do not run two test sessions on the same disposable database.
No execution/strategy/AI e2e test is implied by data-pipeline coverage.


Untrusted source JSON and JSON-encoded outcome/token arrays are bounded to 64
containers in depth. Regression cases include parser recursion failures, the exact
depth boundary, preserved Decimal precision and prior evidence retained through
both collection and offline replay. Excessive nesting is a recorded validation
failure, never an unhandled collection abort.
