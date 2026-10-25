# 29 — V0 live-paper plan and handoff

**Start here if you have no context.** This is the current plan and the single best entry point
for a new session. **Status date:** 2026-10-25. **Authority:** DEC-041 (process), DEC-042 (scope)
in [document 12](12-decisions.md). Everything here is **PAPER ONLY** until the user separately
approves a live pilot.

## 1. What the user wants

1. Test **whether profit can be made by an AI-driven auto-trading system**, and deploy it as
   fast as possible (free or very cheap) so it can be iterated on.
2. The project is built **entirely by AI**. The user can steer architecture and decisions but
   does not write or know Python. Explain decisions plainly; ask for decisions, not code.
3. The system must **learn from its mistakes**: log everything needed, then review losses and
   propose corrections (with human approval of any change).
4. Run **1–2 weeks with fake money**; if results look good, a **real-money pilot** is considered
   as a separate, explicitly approved step.
5. A dashboard with **easy visuals**: which strategy is running, how it is doing, and the ability
   to **turn strategies on and off**.

## 2. Decisions in force

| Topic | Decision |
| --- | --- |
| Market / data | **Kraken public API** for BTC, ETH, SOL (free, no key). Verified reachable from the user's network on 2026-10-25 (ticker). |
| AI | **OpenAI** first, **€10 total cap**, enforced by the application's budget guard. One cheap model, called only when a candidate needs judging. Other providers (e.g. Grok) are deferred. |
| Hosting | **Cheap VPS** (~€4–6/month, vendor and region chosen **together** with the user; check current prices). |
| Evaluation | **1–2 weeks** of paper trading, then a GO / EXTEND / STOP report. Supersedes the 30-day plan. |
| Process | One slice at a time; test → fix → test until green; commit and push after each green slice; keep docs current; the user merges PRs. |
| Money | Paper only. Live trading, real orders, wallets, exchange trading keys are forbidden until separately approved. |

## 3. Where the project is (2026-10-25)

Code: modular Python monolith (`src/ai_trading_lab`), FastAPI + PostgreSQL, ~11.5k lines of source, ~1170
tests, coverage ~95%. Branch `feature/m8-local-runtime`, PR #9 (open, not merged). Milestones M1–M7 are on
`main`.

| Module | Purpose | State for V0 |
| --- | --- | --- |
| `market_data` | Read-only collectors (DexScreener, Solana RPC, Polymarket, Kalshi), raw capture log, destination guard | Built and tested on fixtures. **No Kraken adapter yet.** Polymarket is blocked on the user's network (DNS to loopback). |
| `paper_execution` | Virtual ledger, order book simulation, fees, risk caps, recovery | Built and tested. Reusable as the paper broker. |
| `strategies` | Rule strategies (momentum, probability) and the `StrategyFrame` / `TradeProposal` contracts | Built for offline tapes. Needs a candle-based strategy for Kraken. |
| `ai` | OpenAI/xAI/Anthropic adapters, spend reservations, bounded multi-step flow | Built. **Today the AI can only veto or lower the price of a rule-proposed buy; it cannot originate trades.** V0 must change this (slice 3). |
| `experiments` | Paired A/B cells, cost accounting, immutable configs | Built; useful for comparing rules vs AI. |
| `runtime` | Hash-chained journal, one-owner worker, auto-recovery (DEC-039), health probe | Built but **specific to one Polymarket market** and cannot trade. Must be generalised or replaced by a V0 loop (slice 2). |
| `reporting` | Read-only authenticated dashboard, audit export, Telegram outbox | Built. **No per-strategy view, no on/off toggles, alerts never sent.** |
| `operations` | Backup / restore | Built. |

Not built: a live loop (data → AI/rules → paper trade), strategy on/off, the self-review job, hosting.

Review history and open items from the independent review are in [document 28](28-review-repair-status.md)
(F-08 risk-gating unification and F-11 session chaining are deferred; F-10/F-17/F-22 concern Polymarket and
are moot for Kraken).

## 4. V0 design

**Loop (every N minutes, default 15, per symbol):**
`fetch Kraken ticker + candles + order book` → `build a compact numeric snapshot` →
`rules strategy proposes` and, when a cheap filter flags a candidate, `AI proposes buy / sell / hold with a
reason` → **deterministic code** sizes the order, checks risk limits, simulates the fill with Kraken-like
fees and spread → journal everything.

**Non-negotiables (carried over from ADR-0002):** AI proposes, it never sizes, never touches risk limits,
never produces an executable order. Untrusted text never reaches the model — the snapshot is numbers only.
Missing or stale data means no new entries. Risk breaches latch.

**Two strategies run side by side, each with its own paper portfolio:** a rules-only control and the AI
strategy. Both appear on the dashboard with an on/off switch.

**Costs:** the AI price recorded in the project on 2026-10-19 was `gpt-5.6-terra` at $2.50 / $12 per million
input / output tokens, about $0.009 per decision. Calling the AI every 15 minutes would use the €10 in under
two weeks, so the AI is called only for flagged candidates (target 10–20 calls/day, roughly 10–20 cents/day).
Re-check the current price and model list before activation.

**Numeric risk limits are not yet approved** (docs/12, "proposed choices"). Slice 2 starts by proposing
concrete values (per-trade size, total exposure, daily loss, drawdown halt, stop and time exits) for the
user's approval. Do not invent defaults.

## 5. Slice plan (one at a time; commit + push after each green slice)

| # | Slice | Done when |
| --- | --- | --- |
| 1 | Kraken public data adapter (ticker, candles, order book) with fixtures and contract tests; allow-listed in the transport; destination guard applies | Tests green; one real read-only sample captured as a fixture |
| 2 | Generalised live paper loop: multi-symbol, candle history, deterministic rules strategy with exits (stop, time, take-profit), Kraken fee/spread model, journal, restart-safe | Runs locally for several cycles on real Kraken data; user approves risk numbers first |
| 3 | AI proposer that can originate buy/sell/hold; spend guard (€10); destination guard on the OpenAI client; numeric-only evidence; short ADR for the change from veto-only | AI decisions logged with cost; cap proven by test |
| 4 | Strategy registry with persisted on/off flags and audited toggles; rules vs AI side by side, separate portfolios | Toggle via CLI works and is journaled |
| 5 | **Deploy to the VPS together with the user** (prod Compose, secrets, backups, restart, health) — **starts the 1–2 week clock** | Loop running unattended; health and backup verified |
| 6 | Dashboard v1: strategy cards, on/off switch, equity curve, P&L, trades, AI cost, plain-language status | Authenticated toggle works; browser tests green |
| 7 | Real Telegram alerts (needs the user's bot token) | A test alert received |
| 8 | Self-review job: nightly summary of trades/losses/costs; AI suggests parameter or prompt changes as **new versions** the user approves | One review produced and approved end to end |
| 9 | Evaluation report after 1–2 weeks: paired comparison after all costs, GO / EXTEND / STOP | Report delivered |
| 10 | **Separate approval:** live pilot | Not started; needs explicit go, venue and tax/legal check, security review |

## 6. How to work in this repository

- **Gate** (all must pass before a commit): `ruff format --check .`, `ruff check .`, `mypy`, and
  `pytest --cov=ai_trading_lab` (floor 90%; baseline 95.19%, 1170 tests on 2026-10-25).
- **Test database:** tests need an isolated PostgreSQL named `ai_trading_lab_test`. Start a throwaway one and
  never touch other containers:
  `docker run -d --name atl-test-pg -p 127.0.0.1:55999:5432 -e POSTGRES_USER=review -e POSTGRES_PASSWORD=review_throwaway -e POSTGRES_DB=ai_trading_lab_test postgres:17.9-bookworm`
  then `export ATL_TEST_DATABASE_URL=postgresql+psycopg://review:review_throwaway@127.0.0.1:55999/ai_trading_lab_test`
  and put `.venv/bin` first on `PATH` (the tests call `alembic`). Remove the container afterwards.
- **Python:** use `.venv/bin/...`. `uv` is not installed on the host. The venv's base interpreter lives under
  `/private/tmp` and macOS can clear it; if `.venv/bin/python` aborts at start, recreate the environment.
- **Secrets:** `.env` is git-ignored and holds `ATL_OPENAI_API_KEY` (a personal key, present as of
  2026-10-25). Never print, log or commit values; check only for presence. Use a **separate key with a hard
  monthly limit** on the VPS.
- **Network:** public read-only GETs only, through `HttpTransport`, which refuses non-public destinations. The
  provider and Telegram clients do not have that guard yet (slice 3 / 7).
- **Review:** substantive logic gets an independent fresh-context review (`.codex/agents/code-reviewer.toml`
  or a read-only subagent). It found real defects in this project; do not skip it.
- **Docs:** after every slice update this document's table, `docs/13-progress.md`, and any ADR/decision the
  slice changes. A new session must be able to continue from the docs alone.

## 7. Takeaways from the GRONK guide (third-party, untrusted)

The user's original idea came from a promotional guide ("GRONK BOT TEAM GUIDE"). It describes a desk of seven
prompt-defined agents (CHIEF, SCAN, VET, BOOK, SIZE, FILLS, RISK) run inside a Grok subscription, using
DexScreener, Polymarket and Kalshi data, a local open-source "WorldMonitor" for world context, and one
referral-linked trading app as the only execution venue. Its performance claims are unverified (and partly
inconsistent). Its Grok role is as the agent runtime; it does **not** read X posts — it explicitly refuses
social volume as a ranking input. Borrow the skeleton, not the config:

- Borrow: separate roles (scan / vet / size / fill / risk); the risk role has final authority and acts fast;
  reject-by-default vetting with logged reasons; fee-viability check before every entry; size caps; heartbeat
  and supervision; a report that is assembled from logs, never invented; "degraded data means no entry".
- Do **not** borrow: LLM-driven sizing and risk (ours are deterministic code); a referral venue; memecoin-specific
  rules (holder concentration, pair age) — they do not apply to Kraken majors; the guide's numbers (5% wallet,
  6% size cap, 8% edge, 20% volume exit) are candidate parameters at most, never approved limits.

Grok / X news as a second analyst is a possible later slice. It needs separate paid xAI billing and a deliberate
exception to the project's rule that provider tools are disabled, because posts are untrusted and can contain
prompt injection.
