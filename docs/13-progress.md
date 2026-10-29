# 13 — Project progress and recovery log

**Last updated:** 2026-10-20. Observed results only.

## Current state

- **Milestone:** M7 PR #8 merged as `7d4562f`; M8A local acceptance complete.
- **Branch:** `feature/m8-local-runtime`, base `7d4562f`; work uncommitted/unstaged.
- **Scope:** bounded local paper worker, persistent journal/portfolio/risk, qualified
  observed-source simulation, queued local diagnostics, read-only runtime reporting
  and backup/isolated restore tooling. All acceptance uses synthetic source evidence.
- **Verification:** full make check PASS, **1022 tests,95.36% coverage**, Ruff/mypy;
  production migration/schema/CLI/reporting/backup-restore replay and independent
  review PASS. Final evidence documentation repeats the full gate before handoff.
- **Gates:** no shipping/CI, multi-day run, real source collection, real Telegram,
  paid AI, cloud deployment or live trading occurred or is authorized by M8A.
  The user's existing M7 dashboard remains untouched on its prior verified image.

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


## M4 implementation and local verification — 2026-10-19

User approved document 20. M3 PR #4 is merged as `686f942`; implementation uses
`feature/m4-strategies-backtesting`, with no staging/commit/push/PR action.

Scope/files:

- `strategies/`: frozen versioned momentum/volume-exhaustion, supplied probability
  and cash contracts/rules. Exact UTC/Decimal, explicit thresholds, history and
  availability gates, native outcome/resolution-description identity and explicit
  ownership semantics. No default financial limits or automatic parameter search.
- `backtesting/`: bounded chronological inputs, deterministic sizing, M3 History
  routing, immutable decision/receipt audit and cash/liquidation reporting. Declared
  commit plus installed-source fingerprint preserve dirty-code provenance.
- `paper_execution/replay.py` and existing History/Store: immutable raw-derived
  Polymarket replay manifests, generic collateral units, default fixture-only
  compatibility, research portfolio mutation guard and source-event deduplication.
- Migration `0004_strategy_research`, target metadata, console entry point and
  synthetic probability/momentum JSON examples. No new dependency/service/env var.
- README, architecture, strategy/testing/roadmap docs, DEC-033, document 20,
  ADR-0008 and this progress log record approved boundaries and limitations.

Tests added: 118 collected cases beyond the M3 410 baseline. Strategy equality,
missing/stale/future evidence, history ties, exact units/outcomes/resolution terms,
ownership semantics, deterministic low-precision-context results, sizing/risk,
partial depth, failed exits, expiry, no capacity refresh, end-window marks/fees,
unresolved holdings, bounded CLI parsing/errors, real PostgreSQL atomic rollback,
concurrent duplicates, restart, identity/version conflicts, projection corruption,
coherent appended commands, source-code changes and real-database CLI repeats.
All original M1–M3 cases remain required and passed.

Verification observed on the current application sources:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

PASS: `uv run --locked ruff format --check .`, `uv run --locked ruff check .`,
`uv run --locked mypy` (67 files), and
`uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing`:
**528 passed, 96.29% statement/branch coverage**, no warnings. Integration exercises
migration upgrade → downgrade → upgrade on the isolated test database. Locked
`uv sync --locked --offline` passed without dependency additions.

Production/Compose commands used the existing `atl-m1-validation` project with
`ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000`, leaving unrelated local services alone:

- `docker compose -p atl-m1-validation up -d --build --wait api`: PASS.
- `docker compose -p atl-m1-validation exec -T api /app/.venv/bin/alembic upgrade head`:
  PASS; `alembic current` reports `0004_strategy_research (head)`.
- Container `alembic check`: PASS, no new upgrade operations detected.
- Copied each of `tests/fixtures/research_probability.json` and
  `tests/fixtures/research_momentum.json` into container `/tmp/` and ran
  `/app/.venv/bin/atl-backtest run /tmp/research_<name>.json` twice: both exit 0,
  complete identical reports, exactly two synthetic fills and no open position.
  Installed-source hashes exactly match host source. These are engineering tests,
  not empirical strategy returns.
- `curl --fail --silent --max-time 5 http://127.0.0.1:58000/health/live` and
  `/health/ready`: PASS, PAPER_ONLY, database available, trading_ready false.
- `git diff --check`: PASS. All 30 local Markdown files have valid local link targets;
  `uv lock --check --offline` PASS (53 packages, no lock/dependency changes).
- Hosted CI: NOT RUN for M4; requires a new explicit `/ship` invocation first.

Repair history and review:

- Initial full execution: 516 passed/2 failed because new integration tests lacked
  explicit pytest-asyncio markers; fixed those markers without skipping cases.
- Fresh independent AI review returned FAIL for mutable research portfolios and
  source depth keyed only by capture UUID. Review repair round 1 froze ordinary
  mutation, compared complete reconciled projections against the expected research
  run, and rejected duplicate native book events independently of capture UUIDs.
  Regression tests include a coherently appended command and repeated book bodies
  under different capture IDs. One capture-integrity test was adjusted to use a
  distinct event so it still specifically tests capture collision, independently
  of the new native-event rejection. Final full gate above passed after repairs.
- Strict typing/lint failures while adding tests were corrected before the passing
  gate. No test was deleted, skipped or weakened to obtain a pass.
- Fresh-context independent re-review returned PASS for the complete final code,
  migrations, fixtures, tests and docs. Both P1 findings are resolved with regression
  coverage; no new actionable blockers. Reviewer ran no tests or mutations. This
  review is separate from the parent-owned checks above, with behavioral read-only
  restrictions rather than a claimed enforced separate filesystem sandbox.

Remaining: genuine sufficient historical tapes, archive authenticity, verified
forecast calibration, realistic execution/collateral/fee assumptions and operational
risk selection remain unverified. Current Solana/DexScreener/Kalshi observations do
not qualify for execution where source time or depth is missing. Polymarket replay
uses generic unspecified collateral, never USD/EUR. No genuine historical strategy
validation, profit, paid service, live order or AI activation is claimed. M6 costs,
paired cohorts/statistical metrics and M5 model/agent runtime remain future work.

Next safe step after final local review: ask the user to invoke `/ship` to authorize
verified commit/push/PR/hosted CI. The user merges manually after exact-head checks;
M5 planning/execution then requires its own approval.


Final handoff: dedicated validation API/database services stopped with volumes and
synthetic data preserved. Final documentation-only evidence update repeats the
mandatory full gate before handoff; hosted M4 CI remains NOT RUN pending `/ship`.


## M4 ship invocation — 2026-10-19

The user explicitly selected the repository ship skill, authorizing final checks,
bounded repairs, commit/push/PR and hosted CI for approved M4 only. Branch
`feature/m4-strategies-backtesting`, base/head before shipping `686f942`;
remote main still matches that baseline. No existing M4 PR or unrelated changes
were found; the remote repository remains private.

Shipping repeated `make check`: PASS, **528 tests, 96.29% statement/branch coverage**,
Ruff formatting/lint and strict mypy (67 files). Production image build/startup,
container migration upgrade/head/schema-drift checks and both HTTP health probes
passed. Fresh-run variants of both synthetic research examples executed twice in
the container, each with identical full reports, two fills, no open positions and
installed-source hashes matching the host. These validate engineering only.

All 30 Markdown files have valid local targets; `git diff --check` passed.
Manual intended-file inspection and a limited redacted token/private-key pattern
check found no candidates or unrelated files. No dedicated security/dependency
scanner is configured; this is not a security audit.

Fresh independent shipping review returned PASS without actionable blockers.
The reviewer read the complete tracked/untracked scope and surrounding M3 code,
without edits, tests, network or Git mutations. Review is separate from the parent
checks above; no enforced separate read-only sandbox is claimed. No shipping repair
was required. Commit/PR and hosted CI evidence follow after those actions complete. No merge,
deployment, paid service, live execution or M5 work is authorized.


## M4 shipping evidence — 2026-10-19

Committed/pushed `bc7568f0b07d0a8780cc5f2c601e6b442b64e926` and opened
[PR #5](https://github.com/StefanGramadnikov/ai-trading-lab/pull/5) into main.
The 37 staged files exactly matched the independently reviewed intended files;
indexed contents matched the verified working tree and `git diff --cached --check`
passed. Manual intended-diff inspection and limited token/private-key patterns found
no candidates. No dedicated scanner/security-audit result is claimed.

Observed hosted CI for that exact implementation head:

- [Push run 39263357586](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39263357586): completed SUCCESS.
- [PR run 39263364229](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39263364229): completed SUCCESS.

Both passed locked install, `make check`, Compose validation, Linux production
build/startup, migration and HTTP health. No shipping/CI repair was required.
The documentation-only follow-up triggers new runs; exact final-head CI status
is checked on PR #5 before handoff rather than inferred from these earlier runs.
Nonblocking GitHub Node/action and runner-image migration notices were observed;
no CI/tooling upgrades were added to M4.

The final documentation evidence update repeats `make check` before its commit.
No application/test/migration logic changed after independent review. Dedicated
validation services are stopped after checks, preserving their volumes/data.
Engineering checks do not establish historical profitability, archive authenticity,
calibrated forecasts/fills, operational risk profiles or an approved collateral FX
mapping. No AI runtime, paid services, live execution, deployment or merge occurred.

Next manual step: review and squash-merge PR #5 after exact-head checks pass.
After merge, obtain approval to prepare the M5 AI/provider/agent implementation plan;
no financial credentials or paid provider calls are required by this M4 handoff.


## M5 implementation and review repairs — 2026-10-20

Scope: new typed `ai/` contracts, evidence, three bounded HTTP providers, fixture
provider, durable budget/service, bounded hybrid/sequential/collaborative flows,
immutable run store and CLI. Migration `0005_ai_runtime` adds budget/call/run tables;
M4 sizing was extracted unchanged for reuse through M3 risk/accounting. Settings,
`.env.example`, console entry point, metadata registration and documentation updated.
No dependency, service, framework, scheduler or execution endpoint was added.

Tests add 156 cases beyond M4's 528: provider schemas/errors/refusals/token and
billing uncertainty, bounded orchestration, local key isolation, deterministic
triggering, actual PostgreSQL concurrency, restart/replay, rollback, integrity and
immutable-policy coverage. Tests never call paid APIs or load local activation.

Exact required gate:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

Observed PASS: `uv run --locked ruff format --check .`, `uv run --locked ruff check .`,
`uv run --locked mypy` (85 files), and
`uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing`:
**684 passed, 96.34% coverage**, no warnings. Includes real isolated PostgreSQL
migration downgrade/upgrade and schema checks. Locked dependency sync previously
passed offline (53 packages, unchanged lockfile).

Independent review is an AI agent in fresh context, not mandatory human review.
Its behavioral read-only restrictions are not an enforced separate sandbox.
First review found model-mismatch accounting; repair round 1 checks model/tier
before retaining settleable usage, with contract and PostgreSQL regressions.
That review hit a quota limit before final verdict. A fresh full review found
partial fixture recovery indexing and unsupported billable tool accounting.
Repair round 2 binds fixture responses to logical call positions including replay;
unsupported OpenAI/Anthropic billing features now retain unknown usage/reservations.
Regression tests interrupt between distinct role answers and prove correct restart,
and exercise HTTP-to-budget unknown costs plus blocking subsequent calls. Initial
new-test type/format failures were corrected; no cases skipped/deleted/weakened.
Re-review and production/smoke evidence remain pending below.

Limits: no unattended AI deployment, operational risk-policy selection, calibrated
forecasts, genuine historical profitability or all-expenses evaluation. Costs are
usage-derived USD estimates, separate from simulated cash. Unknown billing blocks
new calls without automatic retry or administrative override. Hosted CI NOT RUN;
requires explicit `/ship`. No Git staging/commit/push/PR update is authorized yet.


Independent fresh-context re-review: PASS, no remaining actionable findings after
repair round 2. Reviewer did not run tests, edit files, access external services or
mutate Git/database state. Parent gate evidence is separate from review.

Production validation (existing `atl-m1-validation`, DB 55432/API 58000):
`docker compose -p atl-m1-validation up -d --build --wait api` PASS;
container `/app/.venv/bin/alembic upgrade head` and `alembic check` PASS, no drift.
A fresh-ID variant of `tests/fixtures/ai_hybrid.json` ran twice through production
`atl-ai run`: identical complete reports, exactly one recorded zero-priced fixture
call, one simulated fill, zero unresolved reservations. HTTP `/health/live` and
`/health/ready` PASS; PAPER_ONLY, database available, trading_ready false.

Before paid smoke, the fixed envelope `a0000000-0000-4000-8000-000000000010`
had zero recorded calls and no budget row. Official OpenAI model documentation
was rechecked 2026-10-20: gpt-5.6-terra supports Responses/structured output and
reasoning none, USD2/M input and USD12/M output; cache writes 1.25x. The existing
price snapshot remains within 24 hours; USD2.5/M conservative input is retained.
No real call is automatically retried; the outcome is recorded next.


## M5 real-provider acceptance — 2026-10-20

After the full gate and independent PASS, executed exactly the approved fixed run:

```bash
ATL_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache uv run --locked atl-ai run tests/fixtures/ai_openai_smoke.json
```

PASS, exit 0: actual OpenAI `gpt-5.6-terra` response validated at
2026-10-20T07:49:35.912187Z. Recorded 797 input / 155 output / 0 cached tokens,
3482 ms monotonic elapsed. Model proposed buy at 0.50 on explicitly synthetic
supplied evidence; deterministic M3 sizing/risk accepted one simulated fill.
Response hash: `fc75f92ff7fe68576cd028f1f1cb96287b7825cf67f957101615d63e1d849035`.
The durable ledger records one call, zero unresolved/remaining reservations and
**USD0.0038525 conservative estimated cost**, inside the approved USD1 total cap.
This uses USD2.5/M input rather than the ordinary USD2/M to cover potential cache
writes; it is not an invoiced-cost statement. No financial credentials were used.

Repeated the exact command: exit 0, entire report identical, call_count still 1;
recorded response replayed without another inference call. The fixed envelope/run
identities are retained; never regenerate them to bypass uncertainty or max_calls.
The local personal key was used through settings, never printed/copied/committed.
Other providers have fixture contract coverage only; no xAI/Anthropic paid activation.
Real market data, forecasts, strategy profitability and unattended operation remain
unverified and outside this synthetic provider-connectivity acceptance.

M5 local acceptance is complete. Remaining: explicit `/ship` for final review/gate,
commit/push/PR and hosted CI; user review/merge; separately approved M6 plan.
Dedicated validation services are stopped with volumes and evidence preserved.


Final documentation consistency review caught a stale README status banner;
repair round 3 updates that banner to local acceptance complete/shipping pending.
Code-review PASS is unchanged; no runtime/test logic changed. Final local links
(33 Markdown files), `uv lock --check --offline` (53 packages) and
`git diff --check` passed. Final full gate repeated after documentation updates.


## M5 ship invocation — 2026-10-20

The user explicitly selected `.agents/skills/ship/SKILL.md`, authorizing shipping
of the approved M5 scope. Branch `feature/m5-ai-runtime`, base/head before commit
`fec599dc61d30dc1afca9d306d2f1d629f593c21`; remote main matches that baseline.
Repository privacy was verified and no existing open M5 PR or unrelated local
change was found. Initial intended scope comprised 37 files; the recovery migration makes 38 files listed in the staged handoff.

Shipping repeated the exact full `make check` command above: PASS, **684 tests,
96.34% statement/branch coverage**, Ruff formatting/lint and strict mypy (85 files).
Production build/startup, container Alembic upgrade/current (`0005_ai_runtime`)/
schema-drift check and both health endpoints PASS. A fresh-ID synthetic AI fixture
ran twice in the production container: identical entire reports, exactly one
zero-priced fixture call, one simulated fill, no unresolved/held reservation.
No additional paid request was made during shipping.

All 33 Markdown files have valid local targets; `uv lock --check --offline` PASS
(53 packages), `git diff --check` PASS. Manual intended-file inspection and a
limited credential/private-key pattern check found no candidates or unrelated
files. No dedicated secret/dependency scanner is configured; this is not a
security audit. The local `.env` is ignored and excluded from the intended files.

Fresh independent shipping review and hosted CI evidence follow when observed.
The final documentation-only evidence update repeats the full gate before commit.
No merge, deployment, M6, recurring provider spend or live trading is authorized.


M5 shipping repair round 1: independent reviewer confirmed that final-transaction
loss allowed changed sizing/controls under the same run ID because only requests,
not the full run, were durable. Added a committed `ai_run_starts` checkpoint before
provider admission, binding full configuration and source fingerprint. New migration
0006 preserves the already applied 0005; completed development identities backfill,
incomplete/malformed legacy records without a provable start fail closed. Added five
PostgreSQL regression cases: interrupted sizing/risk/runtime changes rejected,
exact recovery without another inference, deleted checkpoint refusal and populated
migration preservation. Initial backfill failed on intentionally corrupted previous
test reports; restricting backfill to valid source-hash shapes preserves those rows
and lets runtime refuse them. No test or assertion was weakened.

The actual OpenAI acceptance above predates this recovery-only shipping repair;
provider adapter/HTTP behavior is unchanged. Its stored evidence is preserved.
Changed runtime fingerprints intentionally refuse replay of old reports; no new
paid request or replacement budget envelope was created to repeat acceptance.
Final repaired gate/container/re-review evidence follows before commit.


After shipping repair round 1, `make check` PASS: **689 tests, 96.34% coverage**,
Ruff formatting/lint and strict mypy (86 files). Original M1–M4 cases remain required.
The final count supersedes the initial shipping 684-test gate above.


Final shipping re-review returned PASS after repair round 1, with no remaining
findings across the full scope and surrounding M3/M4 paths. Reviewer ran only a
read-only in-memory reproduction for the original defect, no automated tests,
database/network access or mutations. Verification remains parent-owned.
Rebuilt repaired image/startup PASS; Alembic head `0006_ai_run_starts` and schema
drift check PASS. Repeated fresh production fixture reports are identical and
source fingerprints match the host. Health PASS, PAPER_ONLY. Original approved
smoke ledger still contains exactly one call; no additional paid request occurred.
Final intended scope is 38 files including the new recovery checkpoint migration.
Hosted CI evidence follows after authorized commit/push/PR creation.


## M5 shipping evidence — 2026-10-20

Committed/pushed `9034d3f81ddede3f8684170874a39c42f4fdc516` and opened
[PR #6](https://github.com/StefanGramadnikov/ai-trading-lab/pull/6) into main.
All 38 staged files matched the reviewed working bytes and intended manifest;
`git diff --cached --check` passed. Manual diff inspection and limited credential
patterns found no candidates; no dedicated scanner/security audit is claimed.
No unrelated changes or secrets were included.

Observed hosted CI for this exact implementation head:

- [Push run 39401141487](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39401141487): completed SUCCESS.
- [PR run 39401181900](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39401181900): completed SUCCESS.

Both passed locked sync, full `make check`, Compose validation, Linux production
build/startup, migration and HTTP health checks. No hosted CI repair was needed.
The documentation-only follow-up repeats the full local gate; its exact final-head
CI is verified on PR #6 before handoff, not inferred from these implementation runs.
No runtime, tests, migrations or dependencies changed after independent re-review.

The production fixture repeat/host fingerprint parity passed after repair round 1.
The approved actual OpenAI smoke remains one call at USD0.0038525 conservative
estimated expense; no paid call was added during shipping. Unknown billing/code
changes still fail closed. Validation services are stopped with volumes and audit
evidence preserved after checks.

Remaining limits: synthetic integration does not establish genuine forecast
calibration, strategy profitability, operational risk policies, all-expenses P&L,
venue eligibility or unattended deployment. No live orders, merge or M6 occurred.

Next manual step: review and squash-merge PR #6 after its exact-head checks pass.
After merge, obtain approval to prepare the M6 costs and experiment-evaluation plan.


## M6 implementation in progress — 2026-10-20

User approved the presented M6 plan. Verified M5 merged as
`519ee2ba0d7a9cb6bf73b1a539dab025e83be16c`, then created
`feature/m6-experiments` from remote main with a clean working tree.

Added experiments contracts, explicit profile fractions, paired-dimension checks,
metrics/cost modules, bounded cell runner, lifecycle store, CLI, synthetic four-cell
example and migration 0007. M5's budget store exposes verified per-run costs;
metadata, console entry point, test head, instructions and architecture/testing/
experiment/cost/decision documentation updated. No new dependency or service.

Narrow workers owned only cost+metrics modules/tests and store+migration/tests;
the main session owns integration and verification. Focused cost/metric cases
passed (42 and 31 respectively); lifecycle/store initial 14 PostgreSQL cases passed.
New source edits during one concurrent test attempt correctly triggered source
identity refusal; source changes/testing were subsequently sequenced. A build-index
DNS failure was resolved by `uv sync --locked --offline` (53 unchanged packages),
without adding packages or changing pins. Main's latest offline command:

```bash
PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache uv run --locked pytest tests/unit tests/contract -q
```

Observed **734 passed** before the final shared ownership regression additions;
Ruff lint and strict mypy (105 files) passed. Full `make check` is not yet claimed.

Independent fresh-context review returned FAIL with three confirmed findings.
Repair round 1 adds terminal endpoint valuation/suspension, complete curves/traces
in public reports, and a shared run-ownership registry across M4/M5/M6. The registry
prevents cloned cohorts mixing cost history or appropriating legacy run identities.
M4 pure failed writes retain their prior retry behavior; AI/experiment claims bind
before inference. Added unit, CLI and PostgreSQL regressions for stale endpoint
approval, public curves, concurrent ownership and cross-runner cost isolation.
Re-review of these material repairs is pending.

Docker incident: new focused PostgreSQL attempt stalled in initial Alembic setup;
no tests executed during that attempt. Owned pytest/Alembic/compose processes were
interrupted without altering application data. Bounded daemon checks timed out at
five seconds, including a retry. Asked the user to check/resume OrbStack; independent
code/unit/documentation work continues. Expanded PostgreSQL regressions, complete
`make check`, production build/migration/drift/fixture-repeat/health and final review
remain NOT RUN/pending as appropriate. Do not declare M6 complete until they pass.
No staging, commit, push or PR action has been authorized for M6.


M6 repair round 2 closes a legacy namespace gap: completed `ai_runs` retain their
identity even when no call/start checkpoint exists (e.g. malformed legacy reports
not backfilled by migration 0006). Added a zero-inference legacy regression for
M4/M6 appropriation refusal. No test was skipped, deleted or weakened.

Fresh-context independent re-review returned PASS after round 2 for the complete
tracked/untracked source, migrations, regressions and documentation, including M4/
M5 shared ownership changes. Reviewed runtime fingerprint:
`a32240e028cd3e97c059ba21ce96866c1113e11325ec8bd9ae74c0b518648500`.
The reviewer ran only read-only inspection/in-memory probes, no automated suites,
DB/network calls or mutations. This is code-review evidence, not test acceptance.

Latest verified offline state after repairs:

- `uv run --locked ruff format --check .`: PASS, 143 Python files.
- `uv run --locked ruff check .`: PASS.
- `uv run --locked mypy`: PASS, 105 files.
- `uv run --locked pytest tests/unit tests/contract -q`: PASS, **734 tests**.
- Local targets in 35 Markdown files and `git diff --check`: PASS.

Docker remained unresponsive after another bounded retry. Current repaired
PostgreSQL suite, required full `make check`, production build/migration/drift/
fixture repeat/HTTP health: **NOT RUN / BLOCKED by Docker**. The earlier 14-case
PostgreSQL pass is historical evidence only, not a pass for the final repair.
User action requested: check/resume/restart OrbStack and reply ready. Once healthy,
continue the already approved M6 task with those checks and bounded repairs;
request `/ship` only after local acceptance is genuinely complete. No additional
implementation approval is required to finish these existing steps.


Docker recovery — user asked to retry; daemon responded (29.8.2), isolated DB
started successfully. First resumed full gate: 735 cases passed, 107 setup errors;
all DB tests were blocked by initial migration downgrade. The initial 0007 had
already been applied to the disposable test DB before ownership was added during
review repair, so downgrade tried dropping a nonexistent ownership table.
Repair round 3 restores the original 0007 lifecycle definition and moves ownership
to new migration 0008. No applied migration is repurposed. Repeated full gate and
production checks are now running; no success is claimed before observing results.


## M6 local acceptance after Docker recovery — 2026-10-20

Full required gate after migration repair round 3:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

Observed PASS: 842 tests, 96.53% statement/branch coverage; Ruff formatting/lint
and strict mypy (106 files). All M1–M5 tests remain required. Added two focused
UTC daily-loss cases afterward: a cumulative 6-unit same-day loss breaches the
5-unit cap, while separate-day 2/4 losses reset correctly. Both passed, and fresh
review of this test-only addition returned PASS. Final full-suite counts follow.

Production verification used only the existing `atl-m1-validation` services with
`ATL_POSTGRES_PORT=55432 ATL_API_PORT=58000`, leaving unrelated services alone:

- `docker compose -p atl-m1-validation up -d --build --wait api`: PASS.
- Container `/app/.venv/bin/alembic upgrade head` / `alembic current`: PASS,
  `0008_run_ownership (head)`; `alembic check`: PASS, no new upgrade operations.
- A fresh-ID variant of `tests/fixtures/experiment_architecture.json` ran through
  production `atl-experiment`: create/repeated-create/start, first cell, manual
  pause, refused advance, resume, remaining cells, report twice, replay first key.
  PASS: original snapshots replayed, full reports identical, state EVALUATING,
  evidence_sufficient true for synthetic engineering only, source hash matches
  host `a32240e028cd3e97c059ba21ce96866c1113e11325ec8bd9ae74c0b518648500`.
- Four independent cell fixture calls: **0/1/3/6**; synthetic expense total **0.04**,
  actual expense **0**, counted once. No real provider or invoice claim.
- Every public NAV curve includes the declared endpoint. Stale terminal marks,
  missing FX/expenses, owner collisions and unknown billing fail closed in tests.
- HTTP `/health/live` and `/health/ready`: PASS, PAPER_ONLY, database available,
  trading_ready false. No paid API call was made during M6.

Independent final re-review returned PASS for the complete scope, migration split
and regression additions. Reviewer behavior is read-only; no separate enforced
sandbox or reviewer-run full gate is claimed. Three repair rounds were used:
terminal/identity/report fixes, legacy AI identity guard, applied migration split.
No checks or tests were weakened. No CI run is claimed before `/ship`.

Limits: no verified real-market profitability/calibration, invoice authenticity,
continuous worker or real-time emergency controller, operational risk selection,
recurring provider spending, dashboard/Telegram or deployment. Missing statistical
and valuation evidence stays unavailable. M6 fixture results are engineering only.
Next safe step after final gate: user invokes `/ship` for commit/push/PR/hosted CI.
No further M6 implementation approval is needed; M7 remains separately gated.


Final full gate including the reviewed UTC boundary regressions: PASS,
**844 tests, 96.62% statement/branch coverage**, Ruff formatting/lint and strict
mypy (106 files). This is 155 added cases beyond M5's 689 baseline. No warnings,
skips or weakened assertions. All local Markdown targets (35 files),
`uv lock --check --offline` (53 packages, unchanged pins) and `git diff --check`
passed. The final documentation-only evidence update repeats the full gate before
handoff. Validation services are stopped afterward, preserving volumes and audit.

M6 acceptance is local only. Hosted CI NOT RUN because no M6 `/ship` invocation
has occurred. Work remains uncommitted on the feature branch. Next: ask the user
to invoke `/ship` for final shipping checks, commit/push, PR creation and hosted CI.


## M6 ship invocation — 2026-10-20

User explicitly selected the ship skill for approved M6. Branch
`feature/m6-experiments`, base/head before shipping
`519ee2ba0d7a9cb6bf73b1a539dab025e83be16c`; remote main matches that baseline.
Verified repository privacy, no existing open M6 PR and no unrelated local changes.
Initial intended scope is 38 files, all within the approved M6 change.

Shipping repeated `make check`: initially PASS, 844 tests/96.62%, Ruff/mypy.
Fresh independent review found a daily-loss edge across midnight: a pre-midnight
decision could fill after midnight, then the next frame wrongly reset away that
loss. Shipping repair round 1 resets by execution/valuation UTC date before fills
and preserves exact P&L/fee booking. Two regression cases assert actual fill dates:
2+4 losses on the same execution day breach a 5-unit cap; split execution days do
not falsely breach. Existing deterministic exits remain available.

Repaired full `make check`: PASS, **846 tests, 96.62% statement/branch coverage**,
Ruff format/lint and strict mypy (106 files), with all original cases required.
Production rebuild/startup PASS; repaired migration/replay/health and independent
re-review evidence follow after completion. No paid API call, dependency/service
addition, merge, M7 or deployment is authorized by this invocation.

Local Markdown targets, unchanged lock consistency and diff whitespace checks
passed. Manual intended-file inspection and limited credential patterns found no
candidates; no dedicated secret/dependency scanner or security audit is claimed.
Final staged contents are checked against reviewed working files before commit.


M6 shipping re-review: PASS after repair round 1. The fresh reviewer independently
reproduced both UTC execution-day cases in read-only in-memory probes; no remaining
findings. Reviewed runtime:
`db9c262932a3c8be62d316630cf591f369b9bea95a05be303501eb7e5c0e8c6a`.
Automated gate/production evidence remains parent-owned, separate from review.
No enforced separate read-only sandbox or dedicated scanner is claimed.

Repaired production acceptance PASS: schema upgrade/check, four-cell CLI lifecycle
with manual pause/refused advance/resume, original key replay and two identical full
reports. Fixture calls 0/1/3/6, shared synthetic cost 0.04 counted once, zero actual
expense, host/container source hashes match, health PAPER_ONLY/trading_ready false.
This is engineering evidence, not market performance. No additional paid call.
The documentation evidence update repeats the mandatory gate before commit;
commit/PR and exact-head hosted CI evidence follow when observed.


M6 shipped for review in [PR #7](https://github.com/StefanGramadnikov/ai-trading-lab/pull/7).
Implementation commit `e8bca37f0bf629815d18d653b26d1106700cb090` passed both
[push CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39428172362)
and [PR CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39428198993):
locked dependencies, full quality gate, production Compose build/startup, migrations
and HTTP health. Final pre-commit local gate passed all 846 tests with 96.62%
coverage. Exact staged files matched the reviewed 38-file manifest.

CI emitted non-blocking notices about existing Node action runtimes and the
`ubuntu-latest` image transition. No workflow/dependency upgrades were added to M6.
No dedicated dependency/secret scanner is configured. This documentation-only
shipping record repeats `make check`; its final pushed head must also pass CI
before handoff. Local validation services are stopped afterward, retaining volumes.

Next manual step: user reviews and squash-merges PR #7 after its final checks are
green. Then request approval to prepare the M7 plan; no merge, M7 implementation
or deployment is authorized by this ship invocation.


## M7 implementation — 2026-10-20

User approved the presented six-step plan (“yes, carry on”). Verified PR #7 merged
as `1b401856635e6906d6af73c5a1b21683a14f0c96`, fetched remote main and created
`feature/m7-reporting` with a clean baseline. Recorded document 24 and DEC-037 before
runtime edits. Main owns reporting/API/auth/CLI integration; narrow workers own only
static dashboard/browser cases and notification boundary/migration/tests.

Added bounded reporting reads, existing M6 verification reuse, historical identity
listing with financial refusal, HTTP Basic from local secrets, exact JSON accounting
and audit evidence, static responsive accessible dashboard and single-attempt durable
Telegram outbox. Playwright 1.64.0/pyee 14.0.0 locked as development-only dependencies;
Chromium installed locally, CI includes browser installation. No extra service.

Initial focused reporting API: 13 PASS. Notification unit/contract worker checks:
36 PASS; main owns subsequent complete verification. Initial integration run:
25 PASS, 1 FAIL, 8 setup errors. Failure: read-only reporting invoked legacy
`SELECT FOR UPDATE`; setup errors: Playwright sync loop prevented fixture
`asyncio.run`. Repair round 1 adds an explicit no-row-lock option for snapshot reads
while preserving existing write-path defaults; browser fixture seeding moves to a
dedicated thread. Audit portfolio identities use M6's deterministic experiment/cell
UUIDs. No assertions or tests weakened; repeated affected/full checks follow.

Ruff/mypy currently PASS (123 Python source/test/migration files). Full gate,
production acceptance and independent review remain pending. No real send or paid
call; no staging/commit/push/PR actions.


Repair round 2 fixed audit fill filtering through its order/portfolio relationship
and deterministic primary-key ordering. Full gate then PASS: 921 tests, 96.31%
coverage; Ruff and mypy (123 files) PASS. Production build/migration/schema parity
and fresh four-cell lifecycle replay PASS, with calls 0/1/3/6 and synthetic cost
0.04 once. This evidence predates round 3 and must be repeated after changes.

Independent review found two P2 defects: notification scans reused keys but varied
occurrence timestamps; configured non-ASCII passwords could never authenticate
through FastAPI Basic parsing. Repair round 3 binds signal timestamps to durable
checkpoint time and dated summary timestamps to UTC midnight, rejects scans before
checkpoint, validates ASCII password support at startup, and adds regressions.
Budget verification now borrows the same read-only snapshot connection without row
locks; existing default writer locking remains. New PostgreSQL regression holds a
budget writer lock while reporting reads, then repeats notification scans later
without changing stored events. Mobile visual inspection improved wide audit tables
with local horizontal scrolling. Final gate/production/re-review pending.


## M7 local acceptance — 2026-10-20

Final round-3 code passed the complete required command:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

Observed **924 passed in 22.81s**, **96.35% statement/branch coverage**; Ruff format
(162 Python files), Ruff lint and strict mypy (123 files) PASS. 78 cases added beyond
M6's 846; none removed/skipped/weakened. Includes 8 actual Chromium browser cases
(desktop/mobile × light/dark, authentication, unavailable/corrupt evidence, empty
data, missing values and malicious HTML) plus real PostgreSQL notifications and
read-only reporting/exports. Visual screenshot inspection confirmed responsive
layout, exact values and locally scrollable wide tables.

Fresh independent re-review: PASS, no remaining actionable findings. Reviewer
ran only read-only in-memory probes; actual gate is parent-owned. Third/final
repair round closed both P2 defects and borrowed one read-only budget snapshot.
A nonblocking stale README replay-timestamp instruction was corrected as wording.
No independent enforced sandbox is claimed.

Production acceptance used only the existing dedicated project, preserving other
local services (set ATL_POSTGRES_PORT=55432 and ATL_API_PORT=58000):

- `docker compose -p atl-m1-validation up -d --build --wait api`: PASS with example
  smoke-only reporting credentials; these are not personal secrets.
- Container `/app/.venv/bin/alembic upgrade head` / `alembic check`: PASS,
  head0009_notifications and no schema drift. Previously applied migrations unchanged.
- Fresh four-cell fixture via container `atl-experiment`: PASS, lifecycle pause/
  refusal/resume, original key replay, repeated identical reports and installed/host
  source fingerprint equality. Calls0/1/3/6, synthetic shared expense0.04 once.
- Actual HTTP: protected dashboard/assets/API/export deny unauthenticated access;
  authenticated assets load with no-store headers; details match M6 exact economics;
  two audit downloads are byte-identical with fills and without raw AI traces or
  fixture responses. Both existing health endpoints PASS, PAPER_ONLY and
  trading_ready false. Production excludes Playwright and pytest; packaged assets
  exist. No production UI build service was introduced.
- Container `atl-notify queue UUID --at 2026-10-20T01:00:00Z --warning-fraction 0.8`
  and repeated scan at02:00:00Z returned the identical one pending summary;
  `atl-notify show EVENT_KEY` remains pending. No send command or real Telegram
  request was executed.

Final verified runtime fingerprint:
`587a0ec008f302c87f8643b73ef3b853b46722945b436ac65773072351b1063f`.
Host smoke drivers/logs reside in `/private/tmp/atl-m7-*`; engineering fixtures
are not real performance, real notification delivery or a multi-day operation.
Local Markdown links, lock consistency and diff whitespace PASS. Dedicated secret/
dependency scanners are not configured; no security-audit claim. The documentation
evidence update repeats the full gate before handoff; dedicated validation services
are stopped afterward with volumes retained.

Remaining: real Telegram acceptance requires separate user approval and local
bot token/target. Continuous scheduling, unattended safety monitoring, remote
HTTPS/access hardening and multi-day paper operation belong to M8. No extra paid
AI call, live trading, deployment, staging, commit, push or PR action occurred.
Next safe step: user invokes `/ship` to authorize final verification, commit/push,
PR creation and hosted CI. M8 requires a separately approved plan after merge.


## M7 ship invocation — 2026-10-20

User explicitly selected the repository ship skill. Verified private origin, clean
base `1b401856635e6906d6af73c5a1b21683a14f0c96`, `feature/m7-reporting`, no existing
open branch PR and exact 45-file approved scope. No unrelated files or `.env` are
included. Shipping repeated full `make check`: PASS, **924 tests, 96.35% coverage**,
Ruff format/lint and strict mypy (123 files). No shipping repair was needed locally.

Fresh independent shipping review: PASS with no actionable findings across all
45 tracked/untracked files. Reviewer ran no automated suite, DB or network calls;
execution evidence remains implementer-owned. No separately enforced sandbox or
dedicated security scanner is claimed. Scope fingerprint at review:
`47a1d8226ba658c722e624b3882d846ce544b074f1cb05a7c6e20e813d0b74bb`.
This evidence-only documentation update changes that full-scope hash, not runtime.

Repeated production Compose build/startup, additive migration0009/schema parity,
fresh four-cell lifecycle/replay and host/image source parity PASS. HTTP auth,
packaged assets, exact M6 economics and byte-identical audit exports PASS. Explicit
queue scans at01:00/02:00 reused one pending summary; no send/paid request occurred.
Health remains PAPER_ONLY, trading_ready false. Runtime unchanged:
`587a0ec008f302c87f8643b73ef3b853b46722945b436ac65773072351b1063f`.

Lock consistency (55 packages), whitespace and local links PASS. Manual intended
file inspection and limited credential patterns found no candidates; `.env` is
ignored/excluded. This is not a security audit. Evidence update repeats the full
gate before staging only these reviewed paths, then commit/push/PR and exact-head
hosted CI follow when observed. Merge, real delivery, M8 and deployment remain gated.


## M7 shipping evidence — 2026-10-20

Committed implementation `8590379f81ca9e475f68d38a5e2f8569b023eb6f`, pushed
`feature/m7-reporting` and opened [PR #8](https://github.com/StefanGramadnikov/ai-trading-lab/pull/8).
All 45 staged paths matched reviewed working bytes; cached whitespace PASS.
Final local gate before commit PASS: 924 tests/96.35%, Ruff/mypy. Fresh shipping
review PASS; no local or hosted repair was required for the implementation head.

Exact implementation head hosted evidence:

- [Push CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39442183508): SUCCESS.
- [PR CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39442219783): SUCCESS.

Both installed locked dependencies and Chromium, ran full `make check` including
real browser/PostgreSQL tests, validated Compose, built/started production, applied
migrations and probed HTTP health. Nonblocking notices concern existing Node
action runtimes and the ubuntu-latest image transition; no unrelated upgrades.

This documentation-only follow-up records the observed evidence and repeats the
local full gate. Its actual final pushed head must also pass CI before handoff;
final-head evidence is recorded in PR #8 and the handoff after observation. No
runtime/test/dependency change after independent shipping review. Validation
services are stopped with volumes retained after checks. No dedicated scanner,
real Telegram message, extra paid AI call, deployment, live trading or merge.

Next manual step: review and squash-merge PR #8 when final checks are green.
Then request approval to prepare the M8 operational plan, including continuous
paper scheduling, local multi-day smoke and deployment gates; do not begin it yet.


## M8A start and usage-limit handoff — 2026-10-20

User approved the proposed M8A local-runtime phase with “OK, continue with the
next phase”, then repeated “OK continue” / “continue”. Verified M7 PR #8 merged
as `7d4562fc2040a3e7e91f805f6eb982ba6043c8e5`; fetched origin and created clean
`feature/m8-local-runtime` from remote main. Recorded approved contract in
`docs/25-m8-local-runtime.md` and updated AGENTS current scope.

Account five-hour allowance initially had 7% remaining. All three delegated tasks
then reported the hard account usage limit and stopped. Reported reset: 2026-10-20
20:51 Europe/Sofia. This is an external work interruption, not completed acceptance.

Exact partial artifacts observed at interruption: `runtime/journal.py` and
`operations/backup.py` under src/ai_trading_lab. They are untracked drafts, not
reviewed or integrated. No runtime engine/models/worker/CLI, migration0010, backup
runbook or new tests were observed. Inspect partial files before reuse; no claim
that they implement the approved contracts. M8A tests, quality gate, production
acceptance and independent review: NOT RUN. No staging, commit, push, real market
collection, Telegram delivery, paid AI, operational run or deployment occurred.
Existing M7 local dashboard remains on its prior verified container image.

Resume already approved M8A after account reset; no new implementation approval
is needed. Read AGENTS, document25 and this checkpoint first. Main owns engine
integration/CLI/collection/reporting/tests/docs. Prior narrow assignments: journal
worker owns runtime/journal.py + new0010 migration + PGtests; backup worker owns
operations/backup.py + unit tests + docs26; engine worker owns runtime models/
evidence/reducer + minimal backward-compatible M3History extension + unit tests.
Workers stopped due quota; task messages retain exact contracts. No default paper
risk values may be activated. Finish all implementations, add tests before/alongside,
run full make check with isolated PG, production/fault/restore checks and fresh
independent review before requesting ship. M8B multi-day run, paid AI, real Telegram
delivery, cloud deployment and shipping remain separately gated.


M8A resumed after reset — user confirmed time passed. Account usage access restored.
Reviewed interrupted drafts, resumed narrow workers and integrated source/CLI/worker
with explicit finite activation, borrowed physical-connection ownership and read-only
runtime reporting. No operation was activated. Focused engine/source/CLI/backup tests:
57 PASS. Worker journal PostgreSQL suite:23 PASS, including forced backend termination
and refusal of stale-owner writes. Main combined journal/worker/CLI/source/browser
focused suite:54 PASS in8.89s. Ruff/mypy (140 files) pass before subsequent docs; full
required gate, production/restore acceptance and independent review remain pending.


First full M8A gate: 1015 PASS,1 FAIL (95.28% coverage). The existing immutable
research-portfolio regression expected the established diagnostic, which a widened
runtime ownership guard changed. Repair round1 preserves the existing research
error/behavior and adds a separate runtime-owner refusal; original test unchanged.
Full gate and independent review are repeated; no acceptance claimed yet.


After repair round1, complete make check PASS:1016tests/95.29%, Ruffformat182,
Rufflint and strictmypy140. First controlled production stack `atl-m8-validation`
(DB55433/API58001, separate from user's dashboard) passed build/startup, migration
head0010/schema parity, packaged CLI, fixture position/fee/funding persistence,
original key replay after stop, authenticated runtime UI/API and health. Actual
pg_dump backup, isolated pg_restore, matching checkpoint replay and refusal to
overwrite the existing restore database PASS. Initial acceptance fingerprint
`4088568b62a19e10352158cd64fe0f5e797ce48993976f99a380920ab004c131`.
This predates notification integration, so final current-runtime evidence follows.

Finishing approved reporting integration now queues local diagnostics for suspended
checkpoints from worker/explicit controls; no Telegram delivery. New focused unit
checks13PASS, Ruff/mypy142PASS. Added README/operator examples, synthetic versioned
config/tick, testing/infrastructure/architecture references. Full repeated gate,
final-production repeat and final independent review pending. Initial review found
no actionable code issue but correctly reported acceptance incomplete before those
results/README existed. No shipping/multi-day/paid/live approval is implied.


Repair round2: review confirmed notification enqueue failure could abort otherwise
usable deterministic monitoring after a suspended checkpoint. Queue SQL/time/
validation failures now emit a fixed local diagnostic without leaking exception
text or changing the committed paper control result. A common worker exit hook
also attempts diagnostics for deadline/interrupted-poll terminal paths. Added unit
and real-PG regression with an unavailable outbox: two separately invoked worker
cycles still consume4mocked readonly requests, retain capital and reach the bound.
No real source/Telegram call. Full gate/production/re-review repeated after repair.


## M8A final local acceptance — 2026-10-20

After repair round2, full mandatory command:

```bash
ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@127.0.0.1:55432/ai_trading_lab_test PATH=/private/tmp/ai-trading-lab-tools/bin:$PATH UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache make check
```

Observed **1022 passed in26.58s**, **95.36% statement/branch coverage**, Ruff
format/lint and strictmypy142files PASS. 98 cases beyond M7's924; all prior
assertions remain required, no skipped/weakened tests. Includes nine real Chromium
cases (runtime checkpoint plus prior viewport/theme/error/auth cases), actual PG
worker ownership/recovery tests and deterministic raw-source/engine/backup tests.
Original research-immutability diagnostic preserved after round1. Round2 prevents
outbox failure from stopping qualified monitoring and covers terminal diagnostics.

Fresh independent final review: PASS for all46modified/untracked paths against
`7d4562fc2040a3e7e91f805f6eb982ba6043c8e5`. Reviewer inspected exact gate and final
production evidence, ran only a read-only in-memory reproduction; no independent
full-suite execution, enforced separate sandbox or security-audit claim.

Repaired production acceptance used fresh isolated `atl-m8-final-validation` at
DB55434/API58002 to leave the user's M7 dashboard/DB on58000/55432 untouched:

- Compose build/startup PASS; container Alembic upgrade/current head0010_runtime
  and `alembic check` PASS, no schema drift. Previous migrations unchanged.
- Fresh synthetic config: packaged `atl-runtime create` twice, `step` twice with
  same key/time, restart `show`, pause/resume/stop and replay original step after
  stop PASS. Cash/fees/positions remain persistent; stop does not fabricate exits
  or replenish virtual funding. No fixture is claimed as live market evidence.
- Actual authenticated HTTP dashboard/assets/runtime index/verified checkpoint and
  no-auth401 PASS; existing health remains PAPER_ONLY/trading_readyfalse.
  Production image excludes pytest/Playwright. No worker profile was activated.
- Stop temporary API writer before backup. Actual `BackupTool.backup` using existing
  PostgreSQL17.9container pg_dump/coreutils timeout produced a private custom-format
  archive+hash/count manifest. `restore` created only the fresh dedicated
  ai_trading_lab_restore_test database. Schema/counts matched; replay from that
  database equals the entire original stopped checkpoint. A second restore refused
  the existing target without modifying it. Source/restore retained for inspection.
- Actual restored summary: migration0010, runtime_sessions1/runtime_events4; raw
  sources/paper tables/experiments0 for this isolated scenario. Runtime simulated
  receipts live in its authoritative journal, not legacy offline paper tables.

Final installed/host runtime fingerprint:
`c17ee97a41794bd4c9244668378eb2d1da463050856e2b617237cc30a94e01ac`.
Exact driver/evidence: `/private/tmp/atl-m8-final-smoke.py`,
`/private/tmp/atl-m8-final-production-smoke.log`, `/private/tmp/atl-m8-final-acceptance.json`.
Private backup location/checksum are in that local acceptance file, outside Git.
The earlier pre-notification acceptance remains historical; final evidence above
binds the repaired source. No real collection, paid provider call or Telegram send.

Lock check (55unchangedpackages), local Markdown references and diff whitespace
PASS. No dedicated dependency/secret scanner configured; no security audit claimed.
Final evidence-only docs repeat complete make check before handoff. Temporary M8
validation services are stopped afterward, retaining volumes and backup artifacts;
existing M7 dashboard service remains running. No staging/commit/push/PR/hostedCI.

Limits: no multi-day stability/throughput/physicaldisk quota claim; numeric caps
are engineering ceilings. Worker records only durable progress, not external uptime.
Observed Polymarket raw qualification retains generic collateral; collector supplies
no forecast, so missing evidence abstains. Runtime AI remains disabled. Outbox is
local only; failure emits safe diagnostics without altering deterministic controls.

Next safe step: user invokes `/ship` for final verification/commit/push/PR/CI. After
merge, approve exact risk/source/cadence/storage settings and an explicit M8B local
2–4-day start. M8C cloud/vendor/spending and real Telegram remain separate gates.


## M8A ship gate — 2026-10-20

User explicitly selected the repository ship skill after local acceptance.
Branch `feature/m8-local-runtime`, base `7d4562fc2040a3e7e91f805f6eb982ba6043c8e5`;
46 intended paths. No unrelated changes or dependency/lock upgrades.

Repeated the exact full `make check` command above: **1022 passed in26.31s**,
**95.36% coverage**, Ruff format184/lint and strictmypy142 PASS. Log:
`/private/tmp/atl-m8-ship-check.log`. Local Markdown targets (39 files), offline
locked dependency check (55 packages), diff whitespace and limited credential
pattern inspection PASS. `.env` excluded. No dedicated security scanner configured.

Fresh isolated production project `atl-m8-ship-validation` (DB55435/API58003)
passed image build/startup, migration0010/schema parity, packaged fixture CLI
idempotency/restart/control/replay with retained positions, HTTP auth/runtime and
health, absence of dev tools, actual private pg_dump and new isolated pg_restore,
matching full checkpoint replay and refusal to overwrite an existing restore target.
Same final runtime fingerprint `c17ee97a41794bd4c9244668378eb2d1da463050856e2b617237cc30a94e01ac`.
Evidence: `/private/tmp/atl-m8-ship-production-smoke.log` and
`/private/tmp/atl-m8-ship-acceptance.json`. Temporary services are stopped after
acceptance, retaining volumes/backup; user's M7 dashboard remains untouched.
Fresh independent shipping review PASS with no actionable findings across all46
paths and surrounding contracts. Reviewer inspected evidence but ran no tests;
read-only restrictions were behavioral, not an enforced separate sandbox.
Post-documentation full gate repeated PASS:1022tests in25.84s/95.36%.
Log: `/private/tmp/atl-m8-ship-doc-check.log`. No ship repair rounds required.
Final-head hosted CI will be verified after push and recorded in the associated
PR and completion handoff; it is not claimed green before execution.
No real market acquisition, operational worker, paid inference, Telegram delivery,
multi-day operation, cloud deployment or live trading is part of this ship gate.


## Local operational readiness and manual-action tracking — 2026-10-20

User requested local setup for observation/optimization and explicit notification
when accounts, keys or other manual work become necessary. Keep this checklist
current during subsequent approved phases; identify owner, blocker and completion
evidence, and notify the user at the point their action is required. No unattended
monitor, account creation, operational start or new paid envelope is implied.

M8A PR #9 is OPEN (not merged) when checked. Final head
`1bdda164bbac382bab8fd7564636926435ea6ff4` passed both hosted runs:
[PR CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39477552607)
and [push CI](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39477525348).
Each passed1022tests/95.36%, production build/migrations/health. Existing M7 demo
health on localhost58000 is alive/PAPER_ONLY; this is not an activated M8 runtime.

| Action / readiness item | Owner / timing | Current status |
| --- | --- | --- |
| Squash-merge reviewed PR #9 | User, before M8B setup | Pending; no merge authorization |
| Prepare exact local setup and short smoke plan, then approve bounded activation | Agent prepares; user approves parameters/start | Proposed; no runtime started |
| Preserve existing demo/data, back up, use compatible pinned code/schema/config | Agent, approved setup | Required before updating local installation |
| Confirm source access/current schema, market identity, cadence and storage limits | Agent proposes/proves; user approves operating limits | Pending; public source access not yet proven by M8A fixtures |
| Strategy/forecast inputs and continuous AI integration | Agent scopes; user approves any new work | Current collector generates no forecast; runtime AI disabled; missing inputs abstain |
| Mac power/sleep, OrbStack and network availability | User, immediately before operational run | Manual checklist pending; no OS settings changed |
| Public market-data credentials | Agent checks before selected source activation | No account/key requested for planned public Polymarket reads; no trading account/wallet required |
| Personal OpenAI configuration | User already supplied locally in M5 | One real M5 call recorded; verify local configuration without displaying secrets before reuse; recurring AI/model/budget approval still pending |
| Telegram bot token and recipient | User, when real notification acceptance is approved | Not requested now; real delivery untested; use ignored local environment only |
| Other AI providers / paid data / VPS | User only if selected and approved later | Deferred; no account, key or purchase requested |
| Measure and record optimization evidence | Agent, approved short smoke then 2–4-day run | Pending: collection latency/failures/freshness, cycle duration, CPU/RAM, actual DB/log growth, recovery/replay and dashboard responsiveness; do not invent thresholds or tune strategy for an apparent backtest profit |

Next proposed sequence: merge M8A → approve local setup/short smoke plan → execute
bounded source/readiness test → review measured evidence and blockers → approve
2–4-day M8B operation. AI activation and real Telegram send remain explicit gates.
This entry is an uncommitted documentation update; no runtime/config or GitHub
state changed and no secret was inspected or printed.


Readiness checklist validation: local Markdown links and `git diff --check` PASS.
Required documentation-update `make check` passed Ruff format/lint and strict mypy,
then BLOCKED while preparing the database fixture for browser tests. Docker info
also failed a bounded5second retry (timeout). Stopped only this turn's identified
stalled test/migration/probe processes; no application container/volume cleanup.
Current full-suite recheck is INCOMPLETE, not a new green result. User notified:
check/resume/restart OrbStack and confirm responsiveness before local setup; then
rerun the full gate. Earlier M8A local and exact-head hostedCI evidence remains
historical verified PASS. No application code changed in this documentation task.

## CODEX_FIXES repair execution — 2026-10-23 (in progress)

User approved restoring pinned Python/dependencies and Chromium, then continuing
regression-first M8A repairs. Restored Python3.12.13 under /private/tmp/atl-fixes-python;
preserved broken .venv as /private/tmp/atl-broken-venv-20261022 and recreated with
uv0.11.7 sync --locked (55 resolved packages). Chromium installation completed.
No real source, AI or Telegram calls. No staging/commit/push/branch/PR change.

Created only new disposable postgres:17.9-bookworm server atl-fixes-20261022,
loopback32768/database ai_trading_lab_test, ephemeral tmpfs storage. All previous
demo containers/volumes/data remain untouched. Remove only the new test server
when verification finishes; test URL uses synthetic fixes_test_only credentials.

Verified so far (focused results, not a full-gate claim):

- F01: four behavioral regressions failed before changes; reducing sells and NAV
  simulation no longer use entry-only max_order_notional. New tests verify profitable
  exit, peak NAV/drawdown and experiment result. Focused109PASS; later F01 repeat4PASS.
  Logs /private/tmp/atl-f01-red.log and /private/tmp/atl-f01-green.log.
- F02: nine provenance regressions RED then GREEN; focused332PASS, Ruff/mypy PASS.
  Required explicit capture mode, synthetic transport labeling, observed CLI refusal,
  pending canonical poll/result and active physical worker lease. Existing synthetic
  callers deliberately updated to explicit FIXTURE. Historical audit query documented
  in document27 only; NOT RUN against existing data. Isolated PG ownership test PASS.
- F05: five cross-envelope regressions RED then GREEN; focused91PASS in3.37s,
  Ruff/targeted mypy PASS. Disabled-by-default total budget, configured reviewed tariff,
  durable global serialization/history verification and provider unknown-outcome block.
  User approved guards while preserving ADR0009's prohibition on reconciliation override.
- F04 approved subset:18 health/config unit tests PASS after missing-module RED;
  isolated PG readonly/staleness/terminal/no-writer-lock regression PASS. Compose
  failed-exit restart bounded to3; unhealthy status alone does not restart a container.
  Operational/risk latches are never automatically cleared. No existing service restarted.
- F06: two regressions RED then GREEN. History.last_at orders submitted commands;
  Effect.at and receipt execution time retain simulated latency. Pause/poll can occur
  during that latency. Focused36PASS; logs /private/tmp/atl-f06-{red,green}.log.
- F07: two worker error-path unit regressions RED then GREEN. Known rejected source
  results record a safe failure; concurrent operator stop returns stopped state.
  Integrity/lease/persistence errors remain fatal. Real PG cases/full gate pending.

F03 user decision: larger-limit proposal explicitly approved, maximum configurable
canonical journal512MiB, explicit session settings and worst-case admission budget;
no existing session limits silently widened. Actual database footprint is measured
separately. Compact/incremental journal implementation and realistic benchmark
validation remain in progress; no throughput/capacity result claimed yet.

Automatic repair accounting so far: round1 F02 CLI local-variable type correction;
round2 F04 database-row mapping type correction; round3 F03 test type narrowing.
After round3 verification, further failures require user direction; do not silently
reset the counter or weaken assertions. Full gate/production/restore/review results
and complete finding dispositions will be recorded after actual execution.


### Repair pass verification and stop — 2026-10-23

Detailed dispositions for all22findings, exact commands, limitations and next steps:
[document28](28-review-repair-status.md). Full pytest1078PASS/1FAIL/95.13%; all9
Chromium tests and6000tick real-reducer benchmark ran. Formatting PASS200files;
lint FAIL1unusedimport; mypy FAIL4testtypingerrors. Pytest failure is a deliberate
pending migration-head assertion update (expects0010, new0011isapplied). Coverage
0.07points below supplied review baseline explained in document28; no weakening.

Production image build (approved locked dependency downloads), Compose validation,
production migration up→down→up/schema parity and actual private backup/new isolated
restore/full runtime checkpoint replay PASS. Initial no-network build lacked cached
dependencies; helper first needed explicit repository PYTHONPATH. Final successful
commands/evidence recorded in document28. No existing demo services changed.

Fresh independent interim review INCOMPLETE: confirmed HIGH midnight risk-baseline
rewind during control/in-flight latency and MEDIUM capacity reason lost atdeadline;
F07 real-PG coverage and benchmark latency assertions remain missing. No further
application/test repairs after round3; user direction required. Remaining original
findings, historical-read/report labels and unverified capture work remain pending.
No shipping/activation is implied. Disposable test server removal recorded below.

Cleanup verified: stopped only disposable container
2613639fd49d09dabc578fb0a6c591f5ebeadede17118ae208006f52d27ca5f9
(atl-fixes-20261022); its automatic removal completed after a short asynchronous
wait, and a bounded inspect confirmed it no longer exists. Its tmpfs data was
discarded; private backup artifact remains outside Git. Existing services/volumes
untouched. Local Markdown targets41files and final diff whitespace PASS. All work
remains unstaged/uncommitted. Next action requires user direction under repair cap.

## 2026-10-25 — CODEX_FIXES repair continuation (verified state)

Continued the repair on the same uncommitted tree after Codex stopped at its repair limit.
Fixed and tested: F-09, F-13 (store, report and CLI), F-14, F-15, F-16, F-19, F-21; added the F-12 mark-only
label, F-10 ordering contract test, F-03 benchmark time budgets, worker bound tests, and an
F-18 reporting fixture correction. Gate on this tree: ruff format/check and mypy clean; 1117
pytest passed before the final F-13 guard tests were added, coverage 95.12%. Not repeated:
production image build, Compose validation, backup/restore production verification. Open
decisions (F-04 auto-clear, F-08 risk unification, F-11 chaining, F-12 exit path) and unverified
live-shape questions (F-10, F-17, F-22) are listed in document 28. M8B remains blocked; nothing
committed or shipped.

## 2026-10-25 — DEC-039 bounded automatic recovery (F-04)

User approved auto-clearing of `collection_failure` and `clock_gap` only: three consecutive fresh
qualified ticks (non-duplicate, VALID, advancing event time, within `max_gap_seconds`; the gap-detecting
tick is not counted), never a pause, risk latch, capacity or stop, with a breaker of 5 clears per session
and 3 per rolling hour. Implemented in the runtime reducer, with a queued `recovery` notification and
state fields `recovery_ticks`, `auto_clears`, `auto_clear_breaker`, `auto_clear_times`. ADR-0011 and
documents 06/12/25 amended. A gap during a manual pause no longer converts the pause into a gap.

## 2026-10-25 — DEC-040: M8B is a no-trade soak (F-12 decided)

User approved deferring the exit/settlement path. Code review showed the OBSERVED runtime cannot open
positions (collector supplies no forecast/feature; rules strategies abstain), so exits are unreachable in
M8A. M8B validates infrastructure only and makes no trading or P&L claim. A forecast/feature source, an
exit/settlement path using captured resolution evidence, and a chaining decision (F-11) gate any trading
run; F-08 should be decided before one. Documentation only; no code changed, nothing committed.

## 2026-10-25 — Course correction to a fast live-paper experiment (DEC-041, DEC-042)

The user restated the goal: test whether an AI-driven system can make a profit, deploy fast and cheaply,
paper money for 1–2 weeks, easy dashboard with strategy on/off, and learn from mistakes. A review of the built
system found it was a well-guarded engine with no live loop: the runtime cannot trade, the AI can only veto,
the dashboard is read-only. Chosen: Kraken public data (BTC/ETH/SOL), OpenAI with a €10 cap (a key already
exists in the git-ignored `.env`), a cheap VPS, 1–2 week evaluation, one-slice-at-a-time process with commit and
push after each green slice. Public reads verified reachable from the user's network on 2026-10-25: DexScreener,
Solana RPC, Kalshi, Kraken, Coinbase; Polymarket remains blocked. Committed and pushed the green repair work
(1170 tests, coverage 95.19%) to PR #9. New plan and handoff: document 29. AGENTS.md updated (scope, commit/push
rule, no repair-round cap). No application code changed in this step.

## 2026-10-25 — V0 slice 1: Kraken public data adapter

Branch `feature/s1-kraken-adapter`, cut from `main` after PR #9 merged. Added `Source.KRAKEN`, a five-endpoint
read-only allow-list, `KrakenAdapter` (pair rules, ticker, validated best-first book, closed candles, server-time
event time), collector/CLI wiring (`--pair`), 37 contract tests, a real-capture fixture test and a persistence/identity
integration test. A real read-only capture exposed three things synthetic tests had missed: empty fee schedules,
721-row candle windows, and a tracking cookie that tripped the transport's credentials guard (now fixed: the transport
accepts no cookies). Live capture: 9 GETs, then 11 after the request plan changed, replayed offline to 3 VALID observations. An
independent review then drove: per-pair adjacent server time with look-ahead and staleness checks, strict plain
decimals, plausible-timestamp bounds (a huge candle timestamp used to crash the whole collection), one-sided books
marked PARTIAL, rate limits that stop the collection, `pair` required by the transport, `--pair` only for Kraken, and
an identity-stability test that goes through the adapter. See docs/18, docs/16, ADR-0006 and docs/29.

## 2026-10-25 — V0 slice 2: the live paper loop (branch `feature/s2-live-paper-loop`)

Built in four committed pieces after DEC-043 recorded the approved numbers. A: the simulator lets a spot instrument fill
against a real book, and live-observed portfolios only fill OBSERVED quotes whose raw Kraken captures re-derive the quote.
B: the pure rules breakout strategy, mechanical exits (stop -5%, take-profit +8%, 72 h, 6 h cooldown), sizing and risk state.
C: migration 0012, `LiveStore`, pure decisions, the cycle engine with plan-then-submit crash recovery, the passive benchmark.
D: the single-instance loop runner and `atl-live` CLI. Real runs against Kraken exposed and fixed a clock-ordering bug.
Details and the run instructions are in docs/29 sections 8 and 9. Not yet: the AI strategy (slice 3), strategy on/off and
audit (slice 4), deployment (slice 5), dashboard (slice 6).

Independent review of slice 2 (no critical findings) drove: exits exempt from the cost gate, rejected orders no longer
exhaust the command cap (guard at 900), strategy failure isolation, integrity-error exit codes, lock lease verification,
own-session strategy filtering, ledger-ordered order stamps, OBSERVED-only fills for live portfolios. Full gate:
ruff format/check clean, mypy 190 files clean, 1389 tests passed, coverage 95.15%. See docs/29 section 10.

## 2026-10-25 — V0 slice 3: the AI strategy (branch `feature/s3-ai-proposer`)

Cut from `main` after PR #11 merged. Adds a third live strategy that asks the model for a direction every 4 h per pair
(only when the answer could change what the deterministic code does), through the existing budget guard. New: numeric-only
snapshots, `Proposal`/`decide_ai`/`review_candidates` (pure), `Proposer`, migration 0013 (`live_decisions.detail`: the AI
call, answer, usage and cost behind each decision), a provider destination guard, DEC-044 worst-case settlement of unknown
outcomes with a daily breaker, a configurable price window, and explicit activation (`ATL_AI_ENABLED`, key, cap above zero,
a reviewed price). No paid or network call was made in this slice: tests use a stub model and the fixture provider.
See docs/29 section 11.

## 2026-10-26 — V0 slice 4: strategy on/off with an audit trail (branch `feature/s4-strategy-toggles`)

Cut from `main` after PR #12 merged. `atl-live enable|disable <strategy> --reason` and `atl-live audit`, migration 0014
(`live_strategy_audit`), a toggle and its audit row in one transaction, and wind-down semantics for a disabled strategy
(exits still run, no entries, no model calls). The old "frozen when disabled" test was rewritten. See docs/29 section 12.

## 2026-10-26 — V0 slice 5: deployment files (branch `feature/s5-deployment`)

Cut from `main` after PR #13 merged. Vendor and backup target chosen with the user (Hetzner Cloud EU; backups pulled to the Mac).
Adds `compose.prod.yaml` (db, one-shot migrate, `atl-live` loop; no published ports, read-only, capabilities dropped, fixed
`PAPER_ONLY`), `atl-live health`, `deploy/` (bootstrap, backup with checksum, restore test, pull-from-Mac, kill switch, systemd timer,
env template) and docs/30. Rehearsed end to end locally in an isolated Compose project; the server itself is not yet created.

## 2026-10-28 — V0 deployed (slice 5 completed)

The V0 paper loop runs on the Hetzner server (docs/29 section 14). First real cycle 2026-10-28T16:20:47Z: three pairs observed,
benchmark filled, no errors; backup and restore test passed. Rules and benchmark strategies are running; the AI strategy is off
until activated by the owner. The 1-2 week evaluation clock started with the first cycle.

## 2026-10-28 — V0 slice 6: error journal (branch `feature/s6-error-journal`)

Owner asked for visibility and error tracking before relying on the unattended run, with bounded storage. Adds `error_groups` and
`error_occurrences` (migration 0015), `live/journal.py` (redaction, fingerprints, bounded retention, containment), wiring into the
engine, the AI path and the worker, `atl-live errors` / `ack`, and the journal summary in `status`. DEC-045 raised the AI cap to USD 15.
Slices renumbered: 7 = dashboard, 8 = Telegram alerts, 9 = self-review, 10 = report, 11 = live pilot (docs/29 section 5).

## 2026-10-29 — Slice 6 deployed; slice 7 dashboard (branch `feature/s7-dashboard`)

PR #16 merged and deployed (commit c3cdb69, migration 0015 ran, loop healthy, `atl-live errors` works; the first cycle after the
restart logged three Kraken `source_error` warnings, which the journal captured without a cause: the error carries no detail).
Slice 7 adds the `dashboard` package (sign-in, read models, working switches, acknowledgement, shared alert rules), the static page,
a `dashboard` service published on 127.0.0.1 only, and DEC-046 (alert thresholds approved by the owner). Not yet deployed.

## 2026-10-29 — Slice 7 deployed

PR #17 merged and deployed (commit 42d9f15). Dashboard reachable through the SSH tunnel; the owner signed in. Loop healthy, cycles clean.
Next: slice 8 (Telegram alerts; needs the owner's bot token typed into the server env file) and AI activation (needs the owner's new key).



## 2026-10-29 — Slice 8: Telegram alerts (branch `feature/s8-telegram-alerts`)

Adds the `alerts` package and container (separate process), `alert_state` (migration 0016), `atl-alerts run|test|find-chat`, the
planner that sends each problem once, reminds after 6 h, announces recovery and retries failed deliveries a bounded number of times,
and a daily check message. Not deployed; waits for the owner's bot token (docs/30 section 9c).

## 2026-10-29 — Slice 8 deployed

PR #19 merged and deployed (commit 29b2d43). Telegram alerts enabled; test and daily check messages received. All services healthy.
Observed one contained `malformed_candles` warning (ETHEUR, 13:15 UTC) — to be watched.
Next: AI activation (needs the owner's new OpenAI key with a hard monthly limit; verified model price first).

## 2026-10-29 — Slice 8b: AI model setting, `gpt-6.1-sol` (branch `feature/s9-ai-model-setting`, DEC-047)

Owner chose the GPT-6 family. OpenAI's pricing and model pages (read 2026-10-29): `gpt-6.1-sol` $2.00 / $10 per million tokens, reasoning
effort low..max with no `none`. Adds the setting `ATL_AI_MODEL` (default `gpt-6.1-sol`), a per-model reasoning table in the OpenAI
adapter (`gpt-5.6-terra`: none, `gpt-6.1-sol`: low), a fail-closed check (`ai_model_unsupported`) and raises the default output
allowance from 400 to 2000 tokens (reasoning counts inside it). Two integration-test constants for the reserved amount changed
accordingly; the cap invariant they test is unchanged. Independent review: no confirmed money-safety defect; findings (reasoning allowance exhaustion, model-id mismatch cost, stale README/fixture, comment overstating fail-closed) fixed or recorded in DEC-047; a gpt-6.1-sol smoke fixture added. Gate: ruff, mypy clean, 1675 passed, coverage 95.15%. Not deployed. Not
verified: a real call to the API (accepted `reasoning.effort=low`, returned model id) — the first call after activation will show it.

## 2026-10-29 — Slice 8b deployed; AI activated

PR #21 merged, commit b1b33ea deployed, `ai_enabled` from 14:04 UTC. First 6 real calls to `gpt-6.1-sol` all validated (abstain), about USD 0.015
estimated; no errors. Env-file mishap (wrong lines deleted from stale line numbers; dashboard session secret lost) repaired the same
evening; lesson recorded in docs/29 and docs/30.
Next: let the run proceed; slice 9 (self-review), slice 10 (evaluation report) and a stale-docs cleanup remain. Live pilot is not approved.

## 2026-10-30 — Slice 9: daily review (branch `feature/s9-daily-review`, DEC-048)

Adds the `review` package (pure digest, stores `daily_reviews` and `review_suggestions` via migration 0017, service, `atl-review` CLI) and the hook that runs it
inside the alerts watcher. Deterministic and spend-free; rule-based suggestions are advice only. AI-authored suggestions deferred (DEC-048). Independent review found and we fixed: retries that could never resend (each attempt now has its own outbox key), an unbounded retry on a refused message, no review for a day with no cycles, first-day and catch-up figures (expected cycles, spend as of the day's end), a non-repeatable budget read, no timeout around the job. Gate: ruff, mypy clean, 1707 passed, coverage 95.13%. Merged as PR #23. Not deployed (owner away; deployment needs `dc up -d`).

## 2026-10-30 — Slice 10: evaluation report (branch `feature/s10-evaluation-report`, DEC-049)

Adds the `report` package (pure series maths and verdict, a read-only service, Markdown renderer) and `atl-report`. Common-window, after-cost, paired comparison with
the AI bill netted separately; verdict EXTEND by default, GO/STOP from proposed thresholds (DEC-049, not approved). Independent review findings fixed: the risk stop now comes before the data-quality gate, returns are compared in percent of each starting equity, coverage uses the worst strategy, stale/truncated data extends the run, the AI bill window matches the trade window, an abandoned row of the same kind no longer clamps the window, `--usd-eur nan` rejected, the weak daily-edge guard is real. Built on main after slice 9 (PR #23) was merged. Not deployed; not run against the server's data.
