# 10 — Implementation roadmap and acceptance gates

**Status:** Sequencing APPROVED in principle; milestone effort estimates intentionally omitted. **Current:** M1 foundation implemented locally; hosted CI and review pending.

## Rules for milestone execution

- Each milestone begins only after explicit approval (at least for M1 and any material expansion). Keep changes small and testable.
- Git is established **before implementation**; if initialized clone exists, reuse it, do not run `git init` again.
- Complete only when automated checks genuinely pass and evidence appears in `docs/13-progress.md`.
- Provider purchases and live trading require separate explicit budget/permission gates.

| Milestone | Scope | Definition of done |
| --- | --- | --- |
| **M0: Documentation** | README, AGENTS, specs, ADRs, source guide, process | Documents consistent, reviewed, staged/committed to private repo |
| **M1: Foundation** | Python project, Git/CI, dependency pins, Docker Compose, Postgres, FastAPI health endpoint, test tools | Local stack starts; all tests/checks + GitHub CI pass; zero live credentials |
| **M2: Market data** | Read-only DexScreener/Solana + Polymarket/Kalshi adapters; data schema/store | Contract tests incl. 429/5xx; timestamp and identity integrity; sample data persistence |
| **M3: Paper execution** | Virtual ledgers, order/fill lifecycle, impact and fee models, reconciliation | Deterministic accounting, idempotency, restart and failure injection tests pass |
| **M4: Strategies / benchmarks** | Configured hypotheses, baseline rules-only strategies for both markets, historical replay | Evaluation outputs reproducible; no future data leakage; costs modeled |
| **M5: AI and agents** | Provider abstraction, Hybrid and bounded Multi-agent, sequential/collaborative, continuous/event-driven | Strict schemas, cost breaker, prompt injection tests, deterministic risk gates |
| **M6: Experiments** | Immutable run IDs, paired cohorts, risk profile variants, performance/cost calculations | Matched-period reports; no double-counting; lifecycle and pause logic tested |
| **M7: Reporting** | Dashboard, Telegram, budget/risk metrics, audit exports | E2E paper tests, access control, alerts and cost/report validation |
| **M8: Deployment** | 2–4-day local smoke then cloud paper deployment | Stability, replay/recovery, rollback, backup restore and bounded spending verified |
| **M9: 30-day review** | Evaluate up to 30 days of paper operation (including unresolved outcome marks) | User receives GO/EXTEND/STOP report, with risks and uncertainty |
| **M10: Live pilot (not approved)** | Only if separate affirmative authorization after M9 | Independently approved venue, law/tax, capital, risk, secrets and controls |

## Proposed implementation order nuances

- Both markets are **in architectural scope immediately**, but adapters and strategies can ship in increments. No need to complete all strategies before measuring source quality.
- Begin recording valid market data as early as M2; do not count pre-M5 Hybrid development trades in paired M5+ comparison.
- No all-at-once run of dozens of provider/strategy/risk combinations within €100 experimental expenses.

## M1 task breakdown for Codex (after M0 approval)

1. Check existing repo state: remote, branch, uncommitted changes, Git initialization.
2. Confirm target Mac toolchain (Python version, Docker installation) and choose dependency manager/testing static analysis tools; record rationale.
3. Create minimal source tree, pinned environments, Compose Postgres, DB migration mechanism and FastAPI `/health/live` endpoint.
4. Add unit and isolated Postgres integration tests and `.env.example` placeholders. Strictly no wallet/trading credentials.
5. Establish `make check` or equivalent complete quality gate, pre-commit config, GitHub Actions CI.
6. Verify tests locally, CI passing, doc updates, private repository commit/PR review.

## Codex handoff

Use [14-codex-kickoff.md](14-codex-kickoff.md) when M0 is committed and user authorizes M1.

## Approved M1 implementation plan — 2026-10-17

User approved the plan in chat with “yes, continue”: dedicated feature branch;
Python 3.12 + uv lockfile; typed FastAPI/configuration; paper-only startup validation;
liveness and PostgreSQL readiness; SQLAlchemy/psycopg/Alembic baseline; API + database
Compose services; unit/integration tests; Ruff/mypy/pre-commit and GitHub Actions;
exact quality gate and observed progress updates. No strategies, agents, paid
services, execution adapters or cloud deployment. See ADR-0005 for tool selections.

Acceptance remains pending until hosted CI passes and the work is reviewed. Do not
start M2 or merge without user authorization. See progress log for local evidence.
