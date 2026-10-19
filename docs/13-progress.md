# 13 — Project progress and recovery log

**Last updated:** 2026-10-19. Observed results only.

## Current state

- **Milestone:** M3 deterministic paper execution shipped in PR #4; merge/M4
  approval pending. Implementation push and PR CI passed on `d5ea8d3`; final
  documentation-head checks are verified in PR #4 before handoff. The earlier
  Docker blocker resolved after retrying. M2 merged PR #3 as `d52da05`.
- **Authorization:** user “execute the next phase” approved the presented M3 plan.
  DEC-032 and document 19 record scope. `/ship` is still required for staging,
  commits, pushes and PR changes; the user subsequently invoked ship for M3.
  Merge/M4/live activity remain separate gates.
- **Branch:** `feature/m3-paper-execution`, from merged `origin/main` (`d52da05`).
  Implementation `d5ea8d3` is pushed in
  [PR #4](https://github.com/StefanGramadnikov/ai-trading-lab/pull/4).
  No merge, M4 work or external repository setting change was performed.
- **Safety:** PAPER_ONLY, FIXTURE scenarios only, no financial credentials, orders
  to venues, wallets, paid APIs, strategies, AI runtime or new infrastructure.

## M3 implementation and initial verification — 2026-10-19

Snapshot before runtime recovery. The blocked/not-run statuses in this section
are historical; the completion entry below records the later observed results.

Implemented the approved increments in `paper_execution/`:

- Immutable bounded models, UTC/Decimal validation, canonical identities, explicit
  policy and price/quantity/cash quanta. Binary floats/nonfinite amounts reject.
- Pure IOC depth/spot simulation, conservative rounding, actual rounded-cost gate,
  finite shared spot/per-side book capacity, latency/expiry/freshness checks.
- Balanced per-unit journals, available-cash admission, weighted cost basis,
  gross P&L/fees separated, position limits, pause and per-outcome win/loss/void.
- Authoritative command replay reconstructs economics, controls and provenance;
  all order/fill, journal, event, liquidity/instrument and portfolio projections
  must match before another command. PostgreSQL portfolio locks and atomic writes
  implement duplicate/conflict protection; this database behavior is not yet tested.
- New migration `0003_paper_execution` adds eight tables; old migrations unchanged.
  Alembic target metadata includes them. Existing head assertion advanced to M3.
- Bounded `atl-paper run ...` CLI and explicit nonoperational JSON fixture. Existing
  database configuration reused; no new dependency or service. Each command commits
  independently. Missing/stale/partial M2 data never becomes qualified execution.
- 170 additional collected cases: 151 unit cases plus 19 PostgreSQL integration
  cases, including concurrency, rollback, restart, projection deletion/corruption,
  consumption reset and scenario repeatability. Integration cases are NOT RUN.

Observed checks (final local code):

| Command/check | Result |
| --- | --- |
| `UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache /private/tmp/ai-trading-lab-tools/bin/uv sync --locked --offline` | PASS; existing package rebuilt with atl-paper entry, no dependency addition |
| `.venv/bin/ruff format --check .` | PASS; 79 files |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/mypy` | PASS; 48 files |
| `.venv/bin/pytest tests/unit tests/contract -q` | PASS; 381 tests |
| `.venv/bin/pytest --collect-only -q` | 410 collected; collection is not execution evidence |
| `.venv/bin/alembic upgrade head --sql > /private/tmp/atl-m3-migration.sql` | PASS offline SQL generation only; no PostgreSQL migration claim |
| `.venv/bin/atl-paper --help` | PASS; packaged offline command |
| Pure supplied fixture through History/projections | PASS; synthetic 1-unit holding, basis 10.01 USD, fees 0.02 USD, remaining cash 989.97 USD; not a market or profit result |
| Local Markdown link targets; `git diff --check` | PASS |
| Independent code review after repair round 1 | PASS; code-only, not required-runtime/test certification |
| `make check` / all PostgreSQL tests / migration round trip / schema drift | BLOCKED / NOT RUN; local Docker health endpoint times out |
| Production image/startup/CLI/HTTP health | BLOCKED / NOT RUN for M3; same runtime issue |
| Hosted CI / dedicated scanners / trading performance | NOT RUN; no ship invocation, no dedicated scanner or performance experiment |

Independent initial review found three P1 defects: nominal cost admission missed
rounded fees/depth costs; recovery accepted missing order/fill records or changed
pause state; liquidity counters were not reconciled against history. Repair round
1 added effective post-rounding cost checks and authoritative command replay. New
regression cases cover tiny fees/deep books and missing/corrupt order, control,
settlement, event and capacity projections. Independent in-memory probes accepted
intact history and blocked those corruptions, then returned code-review PASS.
Reviewer made no edits or external calls and ran no write-producing tests. No
separate enforced read-only sandbox or full-gate pass is claimed.

Runtime blocker: `orbctl status` says Running, but Docker `_ping` over the configured
OrbStack Unix socket repeatedly times out after five seconds. The dedicated
`docker compose -p atl-m1-validation up -d --wait db` request has not completed.
The user was asked to check/resume OrbStack. No global runtime restart, unrelated
container/service change, database deletion, replacement infrastructure or weakened
integration gate was used. Test port remains 55432; existing 5432 services untouched.

Remaining authorized work after Docker recovers: real PostgreSQL focused tests and
full `make check`; fix genuine failures within the three-round repair limit;
re-review material fixes; migration/metadata and container/CLI/HTTP probes; update
this log with actual results and stop validation services preserving volumes.
M3 is not complete or ready to ship until those gates pass. No `/ship` requested
while the required runtime checks remain blocked. No financial/manual account setup
is needed; only local container runtime recovery is required.

Historical M2 records below retain their original verification dates and do not
establish M3 test success.

## M3 local verification completion — 2026-10-19

Development handoff before the later explicit ship invocation recorded below.

After the user suggested retrying following possible laptop sleep, Docker's Unix
socket `_ping` returned OK and the dedicated validation PostgreSQL became healthy.
No global runtime restart, new infrastructure, credentials or destructive operation
was needed. No application/test changes were needed after recovery.

Full command:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

| Check | Observed final result |
| --- | --- |
| `uv run --locked ruff format --check .` | PASS; 79 files |
| `uv run --locked ruff check .` | PASS |
| `uv run --locked mypy` | PASS; 48 files |
| `uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing` | PASS; 410 tests, 95.57% statement/branch coverage, no warnings |
| All 19 M3 PostgreSQL integration cases | PASS; concurrent spend/sell, duplicates, rollback, restart, settlement, corruption/deleted rows, depth reset and CLI repeatability |
| Isolated migration upgrade → downgrade → upgrade | PASS in full suite, head `0003_paper_execution` |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation build api` | PASS |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation up -d --wait api` | PASS; API/PostgreSQL healthy |
| `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/alembic upgrade head` | PASS |
| `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/alembic check` | PASS; no new upgrade operations detected |
| HTTP `/health/live` and `/health/ready` on 127.0.0.1:58000 | PASS; 200, PAPER_ONLY, trading_ready false |
| `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/atl-paper run /dev/stdin < tests/fixtures/paper_scenario.json` twice | PASS; both exit 0, reconciled true, identical final state/sequence; no duplicate fill |
| Markdown local targets and `git diff --check` | PASS |
| Independent review | Code repair round 1 and final full-scope review PASS; no subsequent runtime/test logic changes |
| Hosted CI and dedicated scanners | NOT RUN; M3 is uncommitted, no /ship invocation, no configured dedicated scanner |

The container scenario is synthetic: opening 1000 virtual USD, one-unit holding,
10.01 cost basis, 0.02 trading fees, 989.97 remaining cash. This is an engineering
fixture, not a market price or performance observation. All observed/replayed
market execution quotes remain refused by this M3 fixture harness. No live venue
execution, provider credentials, funded wallets, paid APIs, operational risk-profile
activation or strategies were introduced.

Dedicated validation API/database services were stopped after checks; the named
volume, existing test/application data and built image are preserved.

Updated README, architecture/testing/roadmap/decisions, document 19 and ADR-0007
with explicit accounting, bounded replay and nonoperational assumptions. Earlier
runtime incident and initial failed review are preserved above. Final full-scope read-only review returned PASS with no blocking findings or scope
expansion. The reviewer did not independently rerun the parent-owned automated
checks. The final handoff uses the full actual gate, not the earlier unit-only result.

Next authorized boundary: wait for a fresh user `/ship` invocation before staging,
committing/pushing and opening the M3 PR; inspect hosted CI then. Merge/M4 require
separate approval. No manual Docker/account/credential setup remains necessary.

## M2 implementation and evidence — 2026-10-18

Development snapshot before `/ship`; later shipping results below supersede the
earlier test count and pending-shipping status in this section.

Scope follows [the implementation plan](18-m2-market-data.md) written before code:

- `market_data/models.py`, `transport.py`, `adapters/`: typed recursively immutable
  captures/instruments/observations, Decimal values/units, separate UTC timestamps,
  explicit freshness, bounded hardcoded public read allowlist, retries/rate-limit/
  degraded state and four source adapters. Native identities are validated.
- `storage.py`, `replay.py`, `collector.py`, `cli.py`: atomic append-only writes,
  definition versioning, duplicate receipt versus distinct poll, sanitized failure
  runs, explicit bounded collection and strict offline replay without HTTP fallback.
- Migration `0002_market_data`, Alembic metadata and matching indexes; old migration
  remains unchanged. Added four tables. Existing locked httpx2 moved from dev to
  runtime; `atl-data` console entry added. No new dependency package/version needed.
- 216 new tests across models, transport, four synthetic source contracts, CLI,
  replay and real PostgreSQL persistence. Shared test session migrates only the
  exact disposable `ai_trading_lab_test`; existing assertions now expect M2 head.
- README, architecture/testing/infrastructure, roadmap, decision/source register,
  ADR-0006, this log and AGENTS reflect M2. Local optional `data/` captures ignored.

Full required local command (disposable credentials only):

```bash
PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test make check
```

| Check | Observed result |
| --- | --- |
| `uv sync --locked` | PASS; 53 resolved packages, existing httpx2 promoted to runtime |
| `uv run --locked ruff format --check .` | PASS |
| `uv run --locked ruff check .` | PASS |
| `uv run --locked mypy` | PASS; 34 source/test/migration files |
| `uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing` | PASS; 234 tests, 95.80% statement/branch coverage, no warnings |
| Test migrations | PASS; upgrade → downgrade → upgrade, exact Decimal round trips, collision rollback, replay and failed CLI run persistence |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation build api` | PASS; runtime httpx2 and console entry packaged |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation up -d --wait api` | PASS; API + PostgreSQL healthy |
| `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/alembic upgrade head` | PASS |
| `curl --fail --silent --max-time 5 http://127.0.0.1:58000/health/live` and `/health/ready` | PASS; 200; PAPER_ONLY, trading_ready false |
| `ATL_DATABASE_URL=<dedicated smoke URL> .venv/bin/alembic check` | PASS; no new upgrade operations detected |
| Public reads + offline replay | See results below; successes and failures persisted distinctly |
| Independent AI review | Initial FAIL, three P2 findings; repair round 1 fixed all, affected and final full-scope re-review PASS |
| Hosted CI for M2 | NOT RUN; local uncommitted work awaits /ship |
| Dedicated dependency/secret scans, trading e2e, deployment/performance | NOT RUN; no configured scanner or trading implementation; no such success claimed |

Public smoke used `ATL_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_m2_smoke`
and `.venv/bin/atl-data collect --source <source> --limit <n> --capture-file <tmp-path>`.
DexScreener/Solana explicitly seeded wrapped SOL mint
`So11111111111111111111111111111111111111112`, limit 1. Prediction samples used
limit 2, max-pages 1. Actual results, not provider uptime guarantees:

- DexScreener: 1 capture + 1 observation, no source failure, PARTIAL unknown event time.
- Solana: 2 captures + 1 supply observation, largest-account read rate_limited (429),
  exit 1; preceding evidence retained. No claim of holder coverage.
- Kalshi: 2 captures + 2 outcome observations, no failure, PARTIAL unknown event time.
- Polymarket: network_error, 0 captures/observations, failed run and exit 1.
  Cause not established; no restriction bypass or credentials requested.
- `atl-data replay /private/tmp/atl-m2-dex-verified.json` persisted REPLAY evidence;
  repeat inserted zero captures/snapshots. Kalshi archive also replayed successfully.
  Temporary captures are untracked and outside the repo. Deterministic source
  contract tests are synthetic and do not establish successful public compatibility.

Review repair round 1: malformed compressed HTTP bodies previously escaped the
transport failure boundary; extreme finite Polymarket timestamps escaped Decimal
error handling; nested JSON dictionaries/lists were mutable despite frozen models.
All were fixed with regression tests, including malformed compression through CLI
into PostgreSQL. The fresh-context AI reviewer independently probed the affected
behaviors and returned PASS without writing files or calling external services.
Final full-scope review also returned PASS after documentation and metadata index
alignment. Parent reran the full gate and production build for the final code.
Parent verification supplies the full gate and smoke evidence. Custom role
restrictions are behavioral; no separate read-only sandbox is claimed.

Environment incident: the local OrbStack runtime reported Running while Docker
and database reads timed out during initial smoke setup. Those initial CLI runs
exited 2; they are not successes. Runtime later responded, migration and explicit
smokes above passed or recorded source failures. No unrelated service was restarted
or data deleted. Validation still uses separate ports 55432/58000; existing port
5432 services were untouched. No extra service or unattended job was introduced. Final container CLI `atl-data --help`
also passed. Dedicated API/database validation services were stopped after checks;
the named volume, test/smoke databases and built image are preserved.

Limitations: public Polymarket compatibility unverified; free RPC throttled;
source-time gaps are PARTIAL rather than current quotes. Archive/database retention,
continuous collection, backup/restore, realistic fills/fees, accounting, strategies
and AI remain deferred. No performance/profit/legal-eligibility claim is made.

Next gate: user invokes `/ship` to verify final scope, commit/push, open the M2 PR
and inspect hosted CI on its exact head. Merge and M3 require separate approval.
No manual credentials, subscriptions or financial account setup are needed.

## M1 implementation — 2026-10-17

- Python src-layout package; Python 3.12.13 and uv 0.11.7; committed lockfile prepared;
  Hatchling 1.32.4 build backend. Local system Python 3.9.6 was left unchanged.
- FastAPI application factory; Pydantic environment configuration with secret redaction,
  validated PostgreSQL URL/timeouts and PAPER_ONLY as the sole accepted mode.
- `/health/live`: process liveness; `/health/ready`: bounded database connectivity.
  Readiness explicitly reports `trading_ready: false`.
- Async SQLAlchemy/psycopg engine, cleanup on shutdown, Alembic baseline
  `0001_foundation`. No domain tables or trading code.
- Dockerfile runs as UID 10001. Compose has API + PostgreSQL 17.9, loopback ports,
  health checks and a persistent named volume. `.env.example` contains local examples only.
- pytest/pytest-asyncio/pytest-cov; Ruff; strict mypy with Pydantic plugin; pre-commit;
  `make check`; GitHub Actions with isolated PostgreSQL, checks and container smoke steps.
- README, agent instructions, requirements/architecture status, testing, infrastructure,
  roadmap, decisions and historical kickoff notes updated. ADR-0005 records tool choices.

## Verified checks

Local bootstrap used `/private/tmp/ai-trading-lab-tools/bin/uv`; Python was installed
under `/private/tmp/ai-trading-lab-python`. The project `.venv` uses that interpreter.
For a lasting developer setup, install uv 0.11.7 on PATH and follow README; temporary
tooling may be removed by the operating system. No global Python was replaced.

The exact full gate invocation used locally (database values are disposable examples):

```bash
PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@localhost:55432/ai_trading_lab_test make check
```

| Check | Result |
| --- | --- |
| `uv sync --locked` | PASS; exact runtime/dev dependencies installed from lockfile |
| `uv run --locked ruff format --check .` | PASS |
| `uv run --locked ruff check .` | PASS |
| `uv run --locked mypy` | PASS; 10 source/test/migration files |
| `uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing` | PASS; 24 tests, no warnings; application statement/branch coverage 100% |
| `uv run --locked pre-commit run --files <all 10 Python files under src/tests/migrations>` | PASS; format, lint, typing hooks |
| YAML parsing of workflow, Compose and pre-commit configuration | PASS |
| `docker compose -p atl-m1-validation config --quiet` | PASS |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation build api` | PASS |
| `ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000 docker compose -p atl-m1-validation up -d --wait api` | PASS; API and database healthy |
| `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/alembic upgrade head` | PASS |
| HTTP container smoke and DB stop/restart | PASS; live/ready 200, DB stopped → live 200 / ready 503, DB restarted → ready 200 |
| `docker compose -p atl-m1-validation run --rm --no-deps -e ATL_EXECUTION_MODE=LIVE api /app/.venv/bin/python -c 'from ai_trading_lab.app import create_app; create_app()'` | Expected nonzero exit with validation error; PASS negative check |
| `git diff --check` | PASS |
| Hosted GitHub Actions | PASS on `382a277`; run [39110146175](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39110146175), 2026-10-18 |
| Trading e2e / cloud deployment / financial performance | NOT RUN; outside M1 scope |

Tests cover configuration defaults, invalid mode/URL/timeouts, `.env` precedence,
secret redaction, liveness/readiness, timeouts/errors, engine disposal, migration
upgrade/downgrade/upgrade, real PostgreSQL reads/rollback and refused connections.
Database tests require the exact disposable database name `ai_trading_lab_test`;
missing configuration fails instead of silently skipping. Do not run concurrent
migration tests against the same database.

## Issues encountered and resolved

- Sandbox blocked branch creation, network downloads and Docker access. Approved
  tool escalations allowed these operations; no approval-review rejections occurred.
- Port 5432 was already allocated. Validation used 55432 and API port 58000 in a
  dedicated `atl-m1-validation` Compose project, without touching the existing service.
- Initial lint/type errors were corrected. Starlette deprecated the older httpx test
  dependency; switched to its supported httpx2 dependency and reran all checks.
- Validation services are stopped (`docker compose -p atl-m1-validation stop`). The
  named volume and built image are retained; no database volume was deleted.

## Remaining gates and next safe step

Current M2 gates are recorded above. M1 evidence below is preserved as history;
it does not establish M2 hosted CI or authorize later milestones.

## Milestone history

| Milestone | Status | Verified evidence |
| --- | --- | --- |
| Requirements gathering | COMPLETE | User's Q1–Q18 answers and explicit additional notes |
| Architecture approval | COMPLETE | User: “I approve this architecture” |
| M0 package draft | CREATED | Initial documentation package |
| M0 committed | VERIFIED 2026-10-17 | Local commit `a58d674`; corrects initial handoff's unverified repo/commit state |
| M1 Foundation | COMPLETE / MERGED | PR #1; merge `f9d0689`; branch and PR CI passed on `5f0f029` |
| Development workflow | MERGED | PR #2, merge `aba5f5d`; explicit ship/review/repair lifecycle |
| M2 Market data | MERGED | PR #3, merge d52da05; final head d4ff5a5 passed push/PR CI |
| M3 Paper execution | SHIPPED / MERGE PENDING | PR #4; 410 tests, 95.57% coverage; implementation push/PR CI passed; verify exact final head in PR |
| M4–M9 | NOT STARTED | Separate approval required |
| M10 live | BLOCKED | Separate authorization required |

## Hosted CI evidence — 2026-10-18

Under the user’s commit/push authorization, committed and pushed `382a277` on
`feature/m1-project-foundation`. `gh run view 39110146175 --json status,conclusion,jobs`
reported `completed` / `success`. The hosted job passed locked dependency sync,
`make check`, Compose validation, image build/startup, Alembic upgrade and HTTP
liveness/readiness probes, then cleaned up its containers. No CI fixes were needed.
This docs-only follow-up records the result; its own CI is checked after push.
No merge, live trading or M2 work was performed.

## Pull-request workflow — 2026-10-18

User requested an M1 PR and a standing rule to open PRs for future authorized
feature/fix/experiment branches. Opened [PR #1](https://github.com/StefanGramadnikov/ai-trading-lab/pull/1)
from `feature/m1-project-foundation` into `main`; recorded the workflow in AGENTS.md
and DEC-027. Pushes needed to create/update those PRs are part of that authorization;
merges still require explicit approval. No application code or tests changed in this
workflow update. `git diff --check` passed; the full suite is repeated by hosted CI
on the pushed documentation update. No live trading or M2 work was performed.

## Completion handoff preference — 2026-10-18

Recorded the user's standing instruction in AGENTS.md and DEC-028: finish each
completed task with a concrete next step and a request for approval, or explicit
manual instructions where user action is needed. PR #1 was initially observed OPEN; the user merged it during this task
(merge `f9d0689`, 2026-10-18). Both CI runs on `5f0f029` passed. M2 has not been authorized. This update changes
documentation only; `git diff --check` passed and CI repeats on push.

## Development workflow step 1 — 2026-10-18

Added `docs/17-development-workflow.md`, a project `ship` skill with implicit
invocation disabled, a project `python-quality` skill and the custom `code-reviewer`
role. Updated AGENTS.md, README, testing standards and decision register. The user
explicitly chose `/ship` in place of automatic push timing and requested independent
review plus test/fix/retest. No staging, commit, push, external PR edit or merge has
been performed in this task. Existing PR #2 was observed OPEN before these changes.

The main session is the default implementer; FE/design roles wait for approved UI
work. QA cases start early. The reviewer is independent but inherits parent
permissions; a custom role alone does not enforce a read-only filesystem sandbox.
Automatic repairs are bounded to three rounds and remain inside approved scope.

Discovery checked OpenAI's curated catalog and the relevant upstream skill text.
`security-best-practices` and `gh-fix-ci` are possible later adoptions; no generic
Python skill appeared in the curated list checked. Polymarket's official skill
includes funded wallet/order workflows, so the whole bundle was not installed.
Market-data-only guidance can be considered during separately approved M2 work.
No suitable official Kalshi skill was established. Skills inspected are listed in
document 17; no upstream skill or SDK was installed.

Validation (2026-10-18):

- Skill Creator `quick_validate.py` passed for both `.agents/skills/ship` and
  `.agents/skills/python-quality`.
- Python `tomllib` parsed `.codex/agents/code-reviewer.toml`; required fields and
  supported capability-disable fields checked. PyYAML verified explicit-only ship
  policy; Markdown link targets checked. Host discovery/loading of these new custom
  files has not been verified in a fresh host session.
- `git diff --check`: PASS.
- Full gate: PASS — Ruff format/lint, strict mypy, 24 tests, 100% application
  statement/branch coverage; no warnings. Exact command uses the existing temporary
  tooling and isolated test database:

  ```bash
  PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@localhost:55432/ai_trading_lab_test make check
  ```

- Independent fresh-context reviewer initially returned FAIL for a documentation
  exemption that could replace the mandatory full gate. Repair round 1 removed the
  exemption, aligned the workflow/testing docs and repeated validation/full checks.
  Reviewer then returned PASS for the full current tracked/untracked change set;
  review does not substitute for the parent's test evidence. Reviewer ran no
  write-producing checks, and no enforced per-agent sandbox is claimed.
- Hosted CI: NOT RUN for these uncommitted changes; `/ship` is required first.
- No application code, dependencies, migrations or CI configuration changed. No
  tests added: this step changes instructions/configuration, validated structurally
  and through review of invocation/failure scenarios rather than wording-matching
  tests. Existing application tests were run as the repository requires.
- Isolated validation database is stopped after checks; its volume is preserved.

At completion of development step 1, shipping still awaited user invocation.
The following entry records the later authorized ship. No external skill, merge,
deployment or M2 work was part of development step 1.

## Workflow shipping invocation — 2026-10-18

The user explicitly selected the project `ship` skill. Scope: the reviewed workflow
instructions, skills, reviewer configuration and supporting documentation only.
Existing PR #2 remains open and will be updated for this expanded final scope.
Base `origin/main` is `f9d0689`; pre-shipping head is `392ef9d`.

A fresh independent AI reviewer examined the complete base-to-head diff, local
changes and all five new files. Review-only verdict: PASS; no blocking findings.
The reviewer did not write files or run write-producing tests, and did not verify
host loading or automated checks. Previous development review had caught and
resolved the mandatory-gate exemption; no shipping repair round was needed.

Final local verification passed: `make check` (Ruff format/lint, strict mypy,
24 tests, 100% application coverage), both skill validators, reviewer TOML,
explicit-only ship policy, Markdown link checks and `git diff --check`.
Commit and hosted CI evidence will be linked from PR #2 after push; no hosted
success is claimed before observing it. Dedicated secret/dependency scanners are
not installed; inspect the reviewed/staged diff manually without calling that a
security audit. No application, dependency, migration or CI changes are included.

After shipping, the next step is review/merge of PR #2 under separate explicit
approval. External skill adoption and M2 implementation remain separate work.

## Update protocol

After each implementation milestone, append date, task/PR or commit SHA, files changed,
exact checks/results, observed performance where applicable, risks, decisions, blockers
and next approved step. Preserve incident history. Resume from the current verified
state; do not infer hosted CI success, trading capability or live authorization.

## M2 ship invocation — 2026-10-18

The user explicitly selected `.agents/skills/ship/SKILL.md`, authorizing shipping
of the approved M2 scope only. Branch `feature/m2-market-data`, base `aba5f5d`;
remote main remains at that base. No unrelated local changes or existing M2 PR
were found; the remote repository is private.

Shipping repeated `make check`: PASS, 234 tests, 95.80% statement/branch coverage,
Ruff format/lint and strict mypy (34 files). Markdown local links and
`git diff --check` passed. Production image build and healthy startup passed.
Final migration/HTTP probes and fresh independent shipping review are recorded
in the PR handoff when complete. Hosted CI has not run before this push.
No paid API, credentials, live trading, deployment or merge is authorized.


Shipping repair round 1: the fresh reviewer found that excessive JSON nesting in
raw captures or Polymarket encoded arrays raised uncaught RecursionError, aborting
collection before failure persistence. Centralized exact JSON parsing now maps
parser recursion errors to validation errors and limits object/array nesting to
64 containers. Added six cases for depth bounds/precision, nested outcome/token
strings, preserved prior observations/captures and the offline replay path.
Focused 59 tests passed; repeated full `make check` PASS: **240 tests, 96.00%**
statement/branch coverage, Ruff format/lint and strict mypy. Earlier 234-test
figures above are development/pre-repair evidence, not the final shipping count.


Independent shipping re-review returned PASS after repair round 1. The reviewer
inspected the repair/tests and reproduced the depth/precision and partial-evidence
behaviors offline without edits, database/network access or write-producing tests.
Final repaired production image build/startup passed; migration and HTTP health
checks passed. Local links and diff whitespace checks passed. Intended changes
are manually inspected before staging/commit; no dedicated secret/dependency
scanner is configured and no security-audit result is claimed.


## M2 shipping evidence — 2026-10-18

Committed/pushed `625b5b08928d29270881abd8aca02f7399e832c9` and opened
[PR #3](https://github.com/StefanGramadnikov/ai-trading-lab/pull/3) into main.
The staged scope matched all 41 reviewed working files, `git diff --cached --check`
passed, and manual inspection found no unintended capture files/credentials.
A limited token/private-key pattern check found no candidates; this is not a
configured dedicated scanner or security audit. No unrelated changes were included.

Observed `gh run list --branch feature/m2-market-data --commit 625b5b08928d29270881abd8aca02f7399e832c9`:
push [run 39164729587](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39164729587)
completed successfully. It passed locked dependency sync, the full quality gate,
Compose validation, Linux production build/startup, migration and HTTP health
checks. PR [run 39164743129](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39164743129)
also completed successfully with all steps passing. The documentation follow-up
triggers new runs; final-head
status is verified in PR #3 before handoff, rather than inferred from this earlier
implementation run. No CI repair was required for the observed successful run.

The M2 source limitations remain: Polymarket public network_error, Solana
largest-account throttling, unknown event-time PARTIAL states. No live execution,
credentials, subscriptions, performance claim or deployment was introduced.
This shipping invocation does not merge or authorize M3.

Next manual step: review and squash-merge PR #3 after its exact-head checks pass.
After merge, obtain approval to prepare the M3 deterministic paper-execution plan.


The documentation-only shipping follow-up repeated `make check`: PASS, 240 tests,
96.00% coverage, Ruff/mypy PASS. No application/test/CI logic changed after review.
GitHub reported nonblocking runner/action deprecation notices; no CI dependency
upgrades were included in M2. Validation service shutdown preserves the named volume.

## M3 ship invocation — 2026-10-19

The user explicitly selected the project ship skill. Approved scope is M3 only,
branch `feature/m3-paper-execution`, base/head before shipping `d52da05`.
Remote main remains at that baseline and no open M3 PR was found. No unrelated
local changes were included in the reviewed scope.

Shipping reran the complete `make check`: PASS, 410 tests, 95.57% statement/branch
coverage, Ruff format/lint and strict mypy (48 files). Production build/startup,
container Alembic upgrade/schema-drift check and HTTP health all passed. A fresh
portfolio variant of the supplied synthetic fixture ran twice through the
container CLI with identical final balances/holdings/sequence and reconciled true.
Local Markdown targets and diff whitespace checks passed.

A fresh independent AI shipping reviewer examined the complete tracked/untracked
change set and returned PASS with no actionable blockers. No shipping repair was
needed. Reviewer evidence is separate from the parent's automated checks, and no
enforced separate read-only sandbox or dedicated scanner is claimed. The previous
implementation repair and Docker incident remain recorded as history.

This entry is prepared for the authorized commit/push/PR. Hosted CI is not claimed
before observing the pushed head; its actual result is recorded in the PR handoff.
No merge, deployment, live trading, credentials or M4 work is authorized.


## M3 shipping evidence — 2026-10-19

Committed/pushed `d5ea8d36036ca040e8922c50e66ded50927a5760` and opened
[PR #4](https://github.com/StefanGramadnikov/ai-trading-lab/pull/4) into main.
The 28 staged files matched the independently reviewed working files and
`git diff --cached --check` passed. Manual intended-diff inspection and a limited
redacted token/private-key pattern check found no credentials or unrelated files.
No dedicated scanner/security-audit result is claimed.

Observed hosted CI for that exact implementation head:

- [Push run 39229139756](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39229139756): completed SUCCESS.
- [PR run 39229176936](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39229176936): completed SUCCESS.

Both ran locked dependency sync, `make check`, Compose validation, Linux production
build/startup, migration and HTTP health checks. No shipping code/CI repair was
required. The documentation-only follow-up triggers new runs; final-head status
is verified on PR #4 before handoff, not inferred from these earlier runs.

Shipping also repeated a fresh-portfolio variant of the synthetic CLI fixture twice
inside the production container: both exit 0, reconciled true, unchanged final
balances, holdings and sequence. Validation services were stopped with volumes and
fixture data preserved. No real-market execution or performance result is claimed.

The fresh independent shipping review returned PASS without blocking findings;
it did not rerun parent-owned automated checks. Accounting/model bounds, fixture-only
quotes, per-unit cash/asset separation and unapproved operational risk profiles
remain as documented in ADR-0007. No paid API, credentials, live execution, strategy,
AI runtime, deployment, repository-setting change or merge was introduced.

Next manual step: review and squash-merge PR #4 after exact-head checks pass.
After merge, obtain approval to prepare the M4 strategies/benchmarks plan.


The documentation-only shipping follow-up repeated the full local `make check`:
PASS, 410 tests, 95.57% coverage, Ruff/mypy PASS. No application/test/CI logic
changed after independent shipping review. Nonblocking GitHub runner/action
deprecation notices were observed; no CI upgrade was included in M3.
