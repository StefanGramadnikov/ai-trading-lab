# 13 — Project progress and recovery log

**Last updated:** 2026-10-18. Observed results only.

## Current state

- **Milestone:** M1 — foundation complete and merged in PR #1; M2 planning/implementation approval pending.
- **Authorization:** User requested M1, required a plan first, then approved the presented
  plan with “yes, continue”. On 2026-10-18 the user additionally approved committing and pushing the feature branch
  to run hosted CI (“yes you can”). Merge, M2, paid APIs and live trading remain unauthorized.
- **Repository:** `/Users/gramadnikov/Development/Research/AI Trading Lab/ai-trading-lab`;
  origin `https://github.com/StefanGramadnikov/ai-trading-lab.git`.
  `gh repo view StefanGramadnikov/ai-trading-lab --json nameWithOwner,isPrivate,defaultBranchRef`
  verified `isPrivate: true` and default branch `main`.
- **Baseline:** `a58d674` — `docs: initialize AI Trading Lab specifications`.
- **Branch:** `feature/task-handoff-approval` (based on merged `origin/main`). M1 merged by the user in PR #1 on 2026-10-18, merge commit `f9d0689`.
  Workflow preference follow-up is on `feature/task-handoff-approval`.
- **Live trading:** NOT AUTHORIZED. No trading/provider accounts, keys or orders used.
- **Current workflow task:** user approved `/ship`, substantive independent review
  and a bounded fix/retest lifecycle. The user explicitly invoked the `ship` skill on 2026-10-18; final checks and
  independent AI review authorize shipping only this workflow step. No external
  skill installation, CI monitor, M2 implementation or merge is included. See DEC-029/030 and document 17.

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

1. Review/merge the documentation-only handoff-preference PR after CI passes.
2. User approval is required to start M2 planning/implementation. Proposed next step:
   prepare a concrete read-only market-data plan for approval before changing application code.
3. No manual credentials or purchases are needed to begin planning.

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
| M1 Foundation | COMPLETE / MERGED | PR #1; merge `f9d0689`; branch and PR CI passed on `5f0f029` |
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
