# 13 — Project progress and recovery log

**Last updated:** 2026-10-18. Observed results only.

## Current state

- **Milestone:** M1 — foundation implemented; local and hosted CI checks pass; review/merge pending.
- **Authorization:** User requested M1, required a plan first, then approved the presented
  plan with “yes, continue”. On 2026-10-18 the user additionally approved committing and pushing the feature branch
  to run hosted CI (“yes you can”). Merge, M2, paid APIs and live trading remain unauthorized.
- **Repository:** `/Users/gramadnikov/Development/Research/AI Trading Lab/ai-trading-lab`;
  origin `https://github.com/StefanGramadnikov/ai-trading-lab.git`.
  `gh repo view StefanGramadnikov/ai-trading-lab --json nameWithOwner,isPrivate,defaultBranchRef`
  verified `isPrivate: true` and default branch `main`.
- **Baseline:** `a58d674` — `docs: initialize AI Trading Lab specifications`.
- **Branch:** `feature/m1-project-foundation`. Implementation commit `382a277` pushed on 2026-10-18; hosted CI passed. No merge.
- **Live trading:** NOT AUTHORIZED. No trading/provider accounts, keys or orders used.

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

1. Review the feature branch: implementation `382a277` and hosted CI both pass.
2. Hosted CI also passed on `51add0f`: [run 39110272436](https://github.com/StefanGramadnikov/ai-trading-lab/actions/runs/39110272436). Verify the latest PR head before merging.
3. Merge requires explicit user approval. M1 implementation/checks are complete;
   review/merge remains pending.
4. M2 and later require separate authorization. No market integrations, strategy/agent
   implementation, cloud purchases or funded trading are included in this change.

Container image tags are version-pinned, not digest-pinned. Hosted Linux/amd64 CI
is now verified. Long-running operation, backup/restore and cloud readiness remain
outside these foundation checks.

## Milestone history

| Milestone | Status | Verified evidence |
| --- | --- | --- |
| Requirements gathering | COMPLETE | User's Q1–Q18 answers and explicit additional notes |
| Architecture approval | COMPLETE | User: “I approve this architecture” |
| M0 package draft | CREATED | Initial documentation package |
| M0 committed | VERIFIED 2026-10-17 | Local commit `a58d674`; corrects initial handoff's unverified repo/commit state |
| M1 Foundation | IMPLEMENTED; LOCAL/CI PASS; REVIEW/MERGE PENDING | `382a277`, CI run `39110146175`; final documentation commit checked separately |
| M2–M9 | NOT STARTED | No results |
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

## Update protocol

After each implementation milestone, append date, task/PR or commit SHA, files changed,
exact checks/results, observed performance where applicable, risks, decisions, blockers
and next approved step. Preserve incident history. Resume from the current verified
state; do not infer hosted CI success, trading capability or live authorization.
