# 13 — Project progress and recovery log

**Last updated:** 2026-10-18. Observed results only.

## Current state

- **Milestone:** M2 read-only market data implemented and locally verified;
  shipping/hosted CI pending. M1 merged PR #1 (`f9d0689`); workflow merged PR #2
  (`aba5f5d`) as confirmed after the user's merge notification.
- **Authorization:** User: “I approve. I also merged it already, you can continue
  to the next phase.” DEC-031 authorizes M2 implementation. `/ship` is required
  before staging/commit/push/PR; M3 and merge remain separate approval gates.
- **Repository:** private `StefanGramadnikov/ai-trading-lab`, existing origin
  `https://github.com/StefanGramadnikov/ai-trading-lab.git`; no reinitialization.
- **Branch:** `feature/m2-market-data`, based on merged `origin/main` (`aba5f5d`).
  M2 changes remain local and uncommitted; no PR exists for this branch yet.
- **Safety:** PAPER_ONLY. No wallets, financial credentials, paid APIs, order paths,
  strategies, AI runtime or new infrastructure. Provider descriptions are untrusted.

## M2 implementation and evidence — 2026-10-18

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
| M2 Market data | LOCAL VERIFIED / SHIPPING IN PROGRESS | 240 tests, 96.00% coverage after ship repair; hosted CI pending |
| M3–M9 | NOT STARTED | Separate approval required |
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
