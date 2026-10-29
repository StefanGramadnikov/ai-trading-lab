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
| DEC-027 | 2026-10-18: open pull requests whenever authorized feature/fix/experiment branches are created | User: “open a pull request and whenever we create such branches, open pull-requests”. Commit/push reviewable work and create/update its PR without another prompt; merging still requires explicit approval. |
| DEC-028 | 2026-10-18: end completed tasks with the recommended next step and an approval request | User requested explicit next-step approval, or clear manual instructions when their action is required. Existing task steps remain authorized; merge/milestone gates remain in effect. |
| DEC-029 | 2026-10-18: use an explicit `/ship` gate for staging, commits, push and PR actions | User answered “use the /ship gate”. Supersedes DEC-027's automatic push timing: PR creation/update remains required when shipping. Ordinary plan/implementation approval does not ship. Separate explicit instructions authorize only the Git/PR action requested. Merge/deployment remain separately gated. |
| DEC-030 | 2026-10-18: independent review and bounded repair are part of the development lifecycle | User approved standard substantive-change review and requested code → tests → fixes → retest with project-relevant skills. First step adds a reviewer role, manual ship skill, Python quality checklist and workflow docs. Maximum three automatic repair rounds; no permanent FE/design team, unattended monitor, external skill installation or M2 implementation in this step. |
| DEC-031 | 2026-10-18: M2 market-data implementation authorized | User merged PR #2 and said “you can continue to the next phase”; implement the previously presented M2 plan on feature/m2-market-data. Public read-only adapters, validated storage, bounded CLI/replay and tests only; no execution, paid services or external skill installation. Commit/push/PR still require /ship. |

| DEC-032 | M3 paper-execution implementation authorized | User: “execute the next phase”, following the presented M3 plan. Deterministic virtual accounting, IOC simulation, recovery, explicit fixture settlement and tests only. No live execution, strategies, AI, operational risk-profile activation or shipping authorization. See document 19. |

| DEC-033 | M4 strategy/backtesting implementation approved | User approved the presented M4 plan, including controlled qualified archived REPLAY without provenance relabeling. Rules-only baselines, immutable research runs, offline sizing/execution and benchmarks only. No live observed execution, paid data, AI, operational profile activation or shipping authorization. See document 20. |

| DEC-034 | M5 AI runtime implementation and real-provider acceptance smoke approved | User approved the presented M5 plan and including a real-provider smoke test. Typed adapters, durable budget controls, bounded Hybrid/sequential/collaborative flows and paper integration. No live trading, shipping, company credentials, purchases or unspecified paid budget. See document 21. |

| DEC-035 | M5 real-provider smoke envelope approved | User selected OpenAI with US$1 total cap. One local personal-key integration smoke only; no company credentials, recurring provider spend or new envelope to bypass an uncertain call. Exact model/pricing recorded before transmission. |

| DEC-036 | M6 experiments and cost evaluation implementation approved, 2026-10-20 | User approved the presented M6 plan. Immutable experiments/lifecycle, paired supplied tapes, explicit paper profile variants, cost/FX allocation, metrics and tests. Fixture AI only; no extra paid calls, operational risk activation, shipping, M7 or deployment. See document 23. |

| DEC-037 | M7 reporting implementation approved, 2026-10-20 | User: “yes, carry on” after six-step plan. Local read-only FastAPI dashboard, authentication, bounded audit JSON, explicit Telegram outbox commands, Playwright development/browser CI tests. No real send, paid AI, scheduler, deployment or shipping; see document 24. |

| DEC-038 | M8A local runtime implementation approved, 2026-10-20 | User approved the M8A plan and repeated continue after quota reset. Bounded local worker, persistent paper portfolio, qualified observed-source simulation, recovery/backup tooling and reporting only. No multi-day run, real Telegram, paid AI, cloud deployment or shipping. See document25 and ADR-0011. |

| DEC-039 | Independent review repair scope and bounded-runtime amendments approved, 2026-10-23 | User authorized regression-first CODEX_FIXES repairs, pinned environment/Chromium restoration, bounded supervised restart and staleness checks, and explicitly approved512MiB maximum configurable journal. Retain per-session limits, manual operational suspension recovery and risk latches. F05 global guards approved without administrative reconciliation override. F11 chaining/F12 new exit-settlement behavior and real source captures remain deferred. No shipping, existing-container changes or operational activation. |

| DEC-040 | M8B is a no-trade infrastructure soak; exit/settlement deferred, 2026-10-25 | User approved (“yes”) after review finding F-12. The OBSERVED runtime cannot open positions (its collector supplies no forecast/feature, so the rules strategies always abstain), so an exit/settlement path is not built before M8B. M8B validates collection, journaling, restart, DEC-039 recovery, notifications and backup over the DEC-016 2–4 day window and carries **no trading, strategy or P&L claim**. Any run that can hold positions first requires (1) an approved independent forecast/feature source, (2) a deterministic exit/settlement path using captured, verified resolution evidence (and a separately approved policy for any latch-driven unwind), and (3) a session-chaining decision (F-11). Narrows document 25's earlier statement that a forecast configuration is an M8B prerequisite: it is now a prerequisite for a trading run only. No multi-day run, shipping or live trading is authorized. |

| DEC-041 | Working process for the fast live-paper experiment, 2026-10-25 | User, answering the process questions: work **one piece at a time**; run tests; **fix and rerun until everything is green**; commit and push after each green piece; keep documentation current enough that a new session with no context can continue; deploy to a small VPS is allowed; evaluate after 1–2 weeks; assistant may commit, push and open/update PRs (the user merges). Branching rule added 2026-10-25: each slice starts on a fresh branch cut from the latest `main`, ends with an open PR that the user merges, and the next slice starts only after that merge. Supersedes the `/ship` gate (DEC-029) and the three-repair-round cap (DEC-030) for this work; independent review of substantive changes stays. Paper-only, deterministic risk, the AI spending cap, no secrets in Git, and anything touching real money remain mandatory. |
| DEC-042 | V0 live-paper scope, 2026-10-25 | User chose **Kraken public data for BTC, ETH and SOL**, **OpenAI** as the first AI provider with a **€10 total AI cap**, a **cheap VPS** (chosen together), and a **1–2 week** paper evaluation (supersedes DEC-016's 2–4 day local plus ~30 day cloud timeline). The AI proposes trades; deterministic code sizes, checks risk and fills. Grok/X news and WorldMonitor context are deferred. Real-money trading is a separate later step needing the user's explicit go, a venue/legal/tax check and a security review. See document 29. |

| DEC-043 | V0 slice-2 numbers approved, 2026-10-25 | User: “Approved: all numbers in the slice 2 proposal as written.” **Market:** Kraken EUR pairs XBTEUR, ETHEUR, SOLEUR, long-only spot, no leverage. **Virtual capital:** €100 per portfolio. **Fees:** 0.80% per side (Kraken Pro lowest tier taker, from kraken.com/features/fee-schedule fetched 2026-10-25, page undated; maker 0.40% is not modelled because the simulator only fills immediately); gross profit and fees are recorded separately and the evaluation adds a lower-fee what-if. **Risk limits (code-enforced, never by the AI):** 20% of equity per position, 40% maximum total exposure, 3% daily loss and 10% drawdown suspensions, stop-loss −5%, take-profit +8%, maximum hold 72 h, 6 h cooldown after a stop-out, at most one entry per symbol per 24 h. **Cadence:** prices every 15 minutes; AI review every 4 h per symbol (slice 3). **Portfolios:** AI strategy, rules breakout strategy and a passive buy-and-hold benchmark, each separate. These are the user's approved paper values for this experiment; they are not live-trading limits. Supersedes the "unapproved numbers" note in documents 06 and 12 for V0 only. |
| DEC-044 | AI call failures are settled at worst case, 2026-10-25 | User chose "Settle at worst case" when asked how slice 3 should treat an AI call with an unknown outcome (timeout, 5xx, dropped connection). The old M5 rule froze every later AI call until someone cleared the call by hand. **Now (live loop only):** such a call is charged at its full reserved maximum against the global cap and recorded as `settled`; the envelope stays usable; a call reserved but never finished (a crash) is settled the same way once it is older than 10 minutes; after 5 settled calls within 24 h the AI pauses until the window passes. The cap stays true because the worst case is already counted. The experiment runner keeps the strict rule (`settle_unknown` is off by default). Also approved with it: `ATL_AI_PRICE_MAX_AGE_DAYS` (default 1, at most 30) lets the owner keep a reviewed price valid for an unattended run. Amends ADR-0009. |
| DEC-045 | AI spending cap raised to USD 15, 2026-10-28 | Owner, on the error-report discussion: "it's fine, I can put 15 USD for example, so we increase the cap". The total AI spending ceiling for V0 (all purposes: trading reviews and any later reporting jobs) is **USD 15** instead of EUR 10, still enforced by the budget guard through `ATL_AI_TOTAL_LIMIT_USD`. Supersedes the cap in DEC-042. |
| DEC-046 | Dashboard and alert thresholds, 2026-10-29 | Owner: "I approve the next slice and your thresholds". Alert rules (shared by the page now and the Telegram sender in slice 8, `dashboard/alerts.py`): **R1** no strategy recorded for 40 min; **R2** one strategy failed 3 cycles in a row (3 errors within 50 min); **R3** newest price of any pair older than 45 min; **R4** 3 unknown AI outcomes in 24 h (the breaker itself stops the AI at 5); **R5** AI budget 80% (warning) and 100%; **R6** an unacknowledged error. No repeat of the same finding within 6 h. Verdict says "too early to tell" while the run is under 72 h or the gap between first and second is under EUR 1. Dashboard: one owner password (scrypt hash in the env file), signed idle-expiring cookie, CSRF, throttle, reached only through an SSH tunnel (published on 127.0.0.1 only); switches and acknowledgements are written with actor `dashboard:owner` to the existing audit tables. Sign-ins are not audited in V0 (no question answered by the owner; revisit before any live step). |

## Architecture Decision Records

- [ADR-0001: Modular monolith and shared experiment infrastructure](adr/0001-modular-monolith.md)
- [ADR-0002: Deterministic risk and paper-first execution](adr/0002-deterministic-risk.md)
- [ADR-0003: Reproducible, staged comparison of AI configurations](adr/0003-experiment-integrity.md)
- [ADR-0004: Testing and Git quality gates](adr/0004-test-and-git-gates.md)

- [ADR-0005: M1 foundation tooling](adr/0005-foundation-tooling.md)
- [ADR-0006: M2 read-only market data and replay evidence](adr/0006-market-data-evidence.md)
- [ADR-0007: M3 paper accounting and recovery](adr/0007-paper-execution-accounting.md)
- [ADR-0008: M4 offline strategy research and qualified replay](adr/0008-offline-strategy-research.md)
- [ADR-0009: M5 bounded AI and durable cost reservations](adr/0009-ai-runtime-and-cost-reservations.md)

- [ADR-0010: M6 paired cohorts and economic accounting](adr/0010-experiment-cohorts-and-economic-accounting.md)
- [ADR-0011: Bounded observation-driven paper runtime](adr/0011-observation-driven-paper-runtime.md)

## Proposed choices requiring later confirmation

- UI choice resolved by DEC-037: lightweight FastAPI-served static dashboard; M1 tooling remains resolved in ADR-0005.
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
