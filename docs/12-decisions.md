# 12 — Decision register

**Version:** 1.0 draft, 2026-10-17. Approved decisions trace to the user's explicit answers in this ChatGPT planning conversation. Proposed details are not silently upgraded to decisions.

## Approved decisions

| ID | Decision | Rationale / note |
| --- | --- | --- |
| DEC-001 | Build for **both Solana memecoins and prediction markets** from the beginning | User chose Q1=C |
| DEC-002 | Target **fully autonomous operation** as a capability; paper first, live approval separate | Q2=A; safety gate from planning |
| DEC-003 | Real-money initial capital **up to €100**, adjustable by explicit future decision | Q3=A+E |
| DEC-004 | Compare **multiple trading strategies**, including GRONK-inspired hypothesis | Q4=C |
| DEC-005 | **Adaptive trading frequency** | Q5=Adaptive |
| DEC-006 | **Controlled self-optimization** with human deployment approval | Q6=C |
| DEC-007 | **Model-agnostic** AI provider adapters | Q7=D |
| DEC-008 | Implement **both sequential and collaborative** multi-agent modes | Q8=C |
| DEC-009 | Autonomous AI research and experimental strategy generation; **controlled deployment** | Q9=C |
| DEC-010 | Test **periodic/continuous and event-driven** AI, cost vs incremental ROI | Q10=C |
| DEC-011 | Develop and run **locally**, smoke test, then deploy same system to cloud | Q11=C, user elaboration |
| DEC-012 | **Dashboard + Telegram** monitoring | Q12=C |
| DEC-013 | **Start simple, design for scaling** | Q13=C |
| DEC-014 | ChatGPT Plus personal; no personal OpenAI/xAI/Anthropic API setup yet; company Claude Pro not for this project | Q14 |
| DEC-015 | Compare **three paper risk profiles**; consider Moderate for eventual live | Q15=D, potentially B |
| DEC-016 | Local smoke **2–4 days**, then cloud paper-trading **up to ~30 days**, followed by explicit decision | Q16 |
| DEC-017 | **Controlled experiment lifecycle**, stop on hard limits, evaluate normal underperformance | Q17=C |
| DEC-018 | Goal is positive **profit after all expenses** and credible commercial viability | Q18=Level 3, C |
| DEC-019 | Every dev cycle: appropriate old/new/regression tests, run and pass all required checks | Explicit user note |
| DEC-020 | Git from the beginning; user creates private repo; tell user to run `git init` if needed | Explicit user note; repo later reported created |
| DEC-021 | One shared platform comparing Hybrid, Multi-agent and Rules-only | Approved architecture review |
| DEC-022 | Deterministic risk sizing/execution and fail-closed new entries; no live without explicit approval | Approved architecture review |
| DEC-023 | Python, Postgres, Compose, FastAPI and lightweight UI **as preferred proposed stack** | Architecture approved conceptually; final package/tool versions M1 |
| DEC-024 | Repository documentation as durable source of truth; `AGENTS.md` for Codex | User requested and approved documentation milestone |
| DEC-025 | 2026-10-17: M1 plan approved by user: “yes, continue” | Python 3.12/uv, mypy, async SQLAlchemy/psycopg, Alembic; API + PostgreSQL only; see ADR-0005. No push/merge authorization. |
| DEC-026 | 2026-10-18: user approved committing and pushing the M1 feature branch (“yes you can”) | Run hosted CI and fix observed failures; merge and M2 still require separate approval. |

## Architecture Decision Records

- [ADR-0001: Modular monolith and shared experiment infrastructure](adr/0001-modular-monolith.md)
- [ADR-0002: Deterministic risk and paper-first execution](adr/0002-deterministic-risk.md)
- [ADR-0003: Reproducible, staged comparison of AI configurations](adr/0003-experiment-integrity.md)
- [ADR-0004: Testing and Git quality gates](adr/0004-test-and-git-gates.md)

- [ADR-0005: M1 foundation tooling](adr/0005-foundation-tooling.md)

## Proposed choices requiring later confirmation

- UI library remains open; M1 Python, dependency manager, type checker, migrations and quality commands are resolved in ADR-0005.
- Numerical risk limits (the earlier profile percentage table is illustrative).
- Precise AI periodic interval and experimental concurrency.
- Dataset retention, VPS vendor, alerting credentials and shared-cost attribution.
- Which provider/model can participate in each paid experiment, depending on API billing.
- Detailed trading/settlement semantics and supported venues for eventual live order routing.

## Known unknowns / blockers

1. RESOLVED 2026-10-17: local clone and origin verified; M0 commit `a58d674` observed. `gh repo view` verified the remote is private with default branch `main`.
2. The source document's performance claims and the FOMO execution route remain **unverified**.
3. Free market data may not reproduce realistic fills or an exhaustive Solana launch universe.
4. Kalshi/Polymarket access, local compliance/taxes and funding requirements for future live activity must be checked independently before authorization.
5. Whether 30 days yields enough resolved prediction events/trades is uncertain; EXTEND is permitted.
6. Aggregate project spending ceiling across all concurrently running provider experiments needs confirmation before any paid API run.

## Change control

For a new user decision, add a new entry with date, authorizing instruction, affected docs/experiment versions and tradeoffs. If a prior decision is superseded, preserve it with a reference to the newer decision; never silently rewrite history.
