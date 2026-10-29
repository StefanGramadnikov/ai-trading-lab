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
| AI | **OpenAI** first, **USD 15 total cap** (DEC-045, raised from €10), enforced by the application's budget guard. One cheap model, called only when a candidate needs judging. Other providers (e.g. Grok) are deferred. |
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
| `market_data` | Read-only collectors (DexScreener, Solana RPC, Polymarket, Kalshi, **Kraken**), raw capture log, destination guard, no-cookie transport | Kraken adapter built and verified on a real capture (slice 1). Polymarket is blocked on the user's network (DNS to loopback). |
| `paper_execution` | Virtual ledger, order book simulation, fees, risk caps, recovery | Built and tested. Reusable as the paper broker. |
| `strategies` | Rule strategies (momentum, probability) and the `StrategyFrame` / `TradeProposal` contracts | Built for offline tapes. Needs a candle-based strategy for Kraken. |
| `ai` | OpenAI/xAI/Anthropic adapters, spend reservations, bounded multi-step flow | Built. In the experiment runner the AI can only veto or lower the price of a rule-proposed buy. **In the live loop (slice 3, section 11) it proposes a direction that deterministic code sizes and executes.** |
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
input / output tokens, about $0.009 per decision. Calling the AI every 15 minutes would use the budget in under
two weeks, so the AI is called only for flagged candidates (target 10–20 calls/day, roughly 10–20 cents/day).
Re-check the current price and model list before activation.

**Fees are not available from Kraken's anonymous API** (slice 1 finding: the schedule arrives empty). The paper
broker therefore needs a configured fee model. Slice 2 must propose it from Kraken's published fee page (to be
fetched and cited, not remembered) for the user's approval, and may use the pair's real `ordermin`, `costmin` and
tick size, which the adapter does provide.

**Numbers approved (DEC-043, 2026-10-25), paper only:** EUR pairs XBTEUR/ETHEUR/SOLEUR long-only; €100 virtual capital;
0.80% fee per side; 20% per position, 40% total exposure, 3% daily-loss and 10% drawdown suspensions; stop −5%,
take-profit +8%, 72 h maximum hold, 6 h cooldown after a stop-out, one entry per symbol per 24 h; prices every
15 min and an AI review every 4 h per symbol; portfolios: AI, rules breakout, passive buy-and-hold benchmark. Any
change needs a new decision.

## 5. Slice plan (one at a time; commit + push after each green slice)

| # | Slice | Done when |
| --- | --- | --- |
| 1 | **Done (merged, PR #10)** Kraken public data adapter (ticker, candles, order book) with fixtures and contract tests; allow-listed in the transport; destination guard applies | Tests green; one real read-only sample captured as a fixture |
| 2 | **Done (merged, PR #11)**: pieces A–D below. Generalised live paper loop: multi-symbol, candle history, deterministic rules strategy with exits (stop, time, take-profit), Kraken fee/spread model, journal, restart-safe | Runs locally for several cycles on real Kraken data; user approves risk numbers first |
| 3 | **Done (merged, PR #12), section 11:** AI proposer that can originate buy/sell/hold; spend guard (€10); destination guard on the OpenAI client; numeric-only evidence; short ADR for the change from veto-only | AI decisions logged with cost; cap proven by test |
| 4 | **Done (merged, PR #13), section 12:** Strategy registry with persisted on/off flags and audited toggles; rules vs AI side by side, separate portfolios | Toggle via CLI works and is journaled |
| 5 | **Done (merged, PR #14; deployed 2026-10-28, section 14).** **Deploy to the VPS together with the user** (prod Compose, secrets, backups, restart, health) — **starts the 1–2 week clock** | Loop running unattended; health and backup verified |
| 6 | **Done (merged, PR #16; deployed 2026-10-29), section 15.** **Error journal and visibility:** every contained exception and every data/AI outage stored in a bounded, redacted journal; `atl-live errors` / `ack` | Problems readable with stack traces and counts; growth bounded; deployed |
| 7 | **Done (merged, PR #17; deployed 2026-10-29), section 16.** Dashboard v1 (design iterated with the owner, `docs/design/`): strategy cards, working on/off switches behind a sign-in, equity curves, P&L, decisions, AI cost, errors view, plain-language status; reached through an SSH tunnel | Authenticated toggle works; browser tests green |
| 8 | **Built on `feature/s8-telegram-alerts`, section 17.** Real Telegram alerts (needs the user's bot token): broken code, anything needing attention, a strategy that stopped working, Kraken or the AI provider unreachable | A test alert received |
| 9 | Self-review job: nightly summary of trades/losses/costs; AI suggests parameter or prompt changes as **new versions** the user approves | One review produced and approved end to end |
| 10 | Evaluation report after 1–2 weeks: paired comparison after all costs, GO / EXTEND / STOP | Report delivered |
| 11 | **Separate approval:** live pilot | Not started; needs explicit go, venue and tax/legal check, security review |

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
- **Branches and PRs:** every slice is built on a fresh branch cut from the latest `main`
  (`git fetch origin && git checkout main && git pull --ff-only && git checkout -b feature/<slice>-<name>`),
  committed and pushed after each green step, and finished with a PR. The user merges the PR; only then does the
  next slice start, on a new branch from the updated `main`. The default branch is `main`.
- **Review:** substantive logic gets an independent fresh-context review (`.codex/agents/code-reviewer.toml`
  or a read-only subagent). It found real defects in this project; do not skip it.
- **Docs:** after every slice update this document's table, `docs/13-progress.md`, and any ADR/decision the
  slice changes. A new session must be able to continue from the docs alone.

## 6b. Known limits

- **Cold recovery time of the runtime journal grows linearly** with the number of cycles and has a fixed 10 s
  bound (`notifications/store.py`, `bounded`). Measured on a GitHub runner: about 3.2 ms per cycle (6.5 s at 2000
  cycles; 6000 exceeds the bound). A cheap VPS will behave like the runner, not like a Mac. A V0 loop that polls every
  15 minutes makes about 1350 cycles in two weeks (about 4 s), which is fine, but design slice 2's persistence so it
  does not depend on whole-history replay, or raise the bound deliberately. The opt-in full benchmark is
  `ATL_BENCH_FULL=1 pytest tests/integration/test_runtime_benchmark.py -s`.

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

## 8. Slice 2 pieces (live paper loop)

| Piece | Content | Status |
| --- | --- | --- |
| A | Simulator fills `spot` instruments against a real book; `KrakenEntry` evidence; `PaperStore` markers and `source_entry` | Done |
| B | Pure rules breakout strategy, deterministic exits (stop −5%, take-profit +8%, 72 h, 6 h cooldown), sizing and risk gates | Done (`live/config.py`, `signals.py`, `risk.py`) |
| C | Tables (strategies, decisions, candles, state, equity), cycle engine with plan-then-submit recovery, passive benchmark | Done (`live/store.py`, `strategies.py`, `engine.py`, migration 0012) |
| D | `atl-live` worker/CLI, real Kraken local run of several cycles | Done (`live/worker.py`, `cli.py`, `status.py`); real runs below |

Implementation details chosen in piece A, not part of DEC-043: the paper broker's per-trade cost gate
(`max_cost_bps`) is set to 250 bps (a round trip costs about 160 bps in fees alone, so spread plus impact above about
0.9% rejects the order), simulated latency is 1 s, quote freshness 60 s, and marketable limit orders are priced 10 bps
beyond the touch so they can walk the book without chasing it.

Definitions fixed in piece B (implementation of the approved numbers; change only by a new decision):
- **Equity** = cash plus the net proceeds of liquidating every position against the current book (fee included).
- **Daily loss** = day-open equity minus current equity, as a share of the *initial* capital, at or above 3%; suspends new
  entries for the rest of the UTC day and clears at the next UTC day. **Drawdown** = peak-to-now equity at or above 10% of
  the peak; it latches until cleared by hand and a recovery never clears it. Exits are never blocked by either.
- **Position size** = the smallest of 20% of equity, the room under 40% total exposure, the broker's per-order cap (20% of
  the *initial* capital, so growth does not enlarge positions) and the cash left after the fee.
- **Stop-loss / take-profit** compare the bid-side liquidation price with the average entry price (cost basis per unit,
  which excludes fees). The most urgent reason wins: stop-loss, then take-profit, then the 72 h limit.
- **Rules breakout strategy (control):** buy when the last closed 15-minute candle closes strictly above the prior 48 h high
  on at least the median volume; sell on a close below the prior 24 h low while held. Missing, stale or gapped candles never
  trade. These strategy parameters are my design for the control strategy, not part of DEC-043.

## 9. Running the live loop (slice 2)

Paper only. `atl-live` needs `ATL_DATABASE_URL` (migrated: `alembic upgrade head`) and `ATL_EXECUTION_MODE=PAPER_ONLY`.
Until the console script is reinstalled in your environment, use `python -m ai_trading_lab.live.cli`.

| Command | What it does |
| --- | --- |
| `atl-live setup` | Creates the two paper portfolios (EUR 100 each) and registers the strategies; safe to repeat, never mutates |
| `atl-live cycle` | One live cycle against real Kraken data, then prints the report |
| `atl-live run --cycles N` / `--until-stopped` | The loop. Runs just after each quarter hour (the newest 15-minute candle has closed), holds a single-instance database lock, contains a failing cycle and exits non-zero after 10 in a row so a supervisor restarts it; SIGINT/SIGTERM stop it after the current cycle |
| `atl-live status` | Strategies, holdings, fees paid, latest equity and recent decisions as JSON |

How a cycle works: collect pair rules, one ticker and, per pair, server time, a 25-level book and candles; store the raw
captures and new closed candles; build each pair's evidence (`KrakenEntry`; a pair whose evidence does not hold together is
treated as having no data); evaluate every **enabled** strategy (rules breakout, passive benchmark); write each order as a
`planned` decision with its intent and raw evidence, submit it to the paper broker, finish the decision, fold the fill into
the loop state and record equity after the fills. Every hold is logged with its reason.

Behaviour worth knowing:
- **Slots.** An order's idempotency key includes the 15-minute slot, so a restart inside a slot never doubles an order, and
  an order that was rejected is retried in the next slot, not repeatedly within the same one.
- **Cycle time** is read after collection (never before a capture it uses); orders in one cycle are stamped 2 s apart because
  the paper ledger's clock cannot move backwards and includes the 1 s simulated latency.
- **Crash recovery.** A `planned` decision found at the start of a cycle is resubmitted with the same key; the broker answers
  a completed order without a second fill and the loop state is restored from the fill.
- **Disabled strategies** are skipped entirely (no decisions); existing positions stay until slice 4 defines switching off.
- **Candles** are fetched incrementally (`since` the last stored candle) and kept as first seen.

First real runs, 2026-10-25 against live Kraken (throwaway database): all three EUR pairs observed with verified evidence and
no failures; the benchmark filled ETH/EUR and SOL/EUR at about EUR 33 each with 0.80% fees and marked equity at EUR 98.88 after
fees and spread. The first run exposed a real bug the fake venue had hidden (the cycle read its clock before collecting, so the
first order's quote looked like it came from the future); it is fixed and has a regression test.

## 10. Review hardening of slice 2 (independent read-only review, 2026-10-25)

- **Cost gate applies to entries only**: a stop, take-profit or time exit is never refused for spread or impact (amends ADR-0007).
- **Live portfolios fill only OBSERVED quotes** whose Kraken captures re-derive the quote (`live_portfolio_requires_observed_quote`);
  spot books are consumed per order, separately from other instrument kinds.
- **Command capacity**: the broker keeps at most 1000 commands per portfolio and stores every submitted order, rejected ones
  included. The engine refuses new orders at 900 (pre-flight refusal is logged as a decision) instead of failing late.
- **Integrity vs rejection**: a broker *rejection* is a normal logged outcome; an inconsistent ledger is a fatal
  `LoopIntegrityError` (CLI exit code 3, 4 for lock loss). The loop never retries past an integrity error.
- **Strategy isolation**: one failing strategy is recorded in `CycleReport.errors` and the others still run. The engine
  evaluates only its own session's strategies (the database may hold other sessions').
- **Order stamping**: orders are never stamped before the ledger's last activity (`last_activity`), since the ledger clock
  cannot move backwards.
- **Lock lease** is verified each cycle; a lost lease stops the worker.
- **Benchmark**: a partial buy counts as held; the rest is retried in the next slot. Disabled strategies: see section 12 (wind-down, since slice 4).
- **Known limits**: no retention policy for decisions or observations (about 600 decision rows per day); the benchmark and
  rules portfolios are independent EUR 100 ledgers.

## 11. The AI strategy (slice 3)

A third strategy, `<session>-ai`, with its own €100 paper portfolio, risk state and journal. Per cycle it runs the same
mechanical exits as the rules strategy first (stop −5%, take-profit +8%, 72 h, risk gates, capacity guard). For a pair that
a decision hangs on, and whose last review was at least 4 h ago (5 minutes of slack), it builds a **numeric-only snapshot**
(bid, ask, spread, closes, 24 h range and volume, changes, the position's entry price, P&L and age, and the fee/stop/limit
rules; at most about 3 kB) and asks the model for buy, sell or hold. The answer is a direction only: a buy is sized by the
same deterministic function as the rules entry (20% of equity, exposure and cash limits, marketable limit), a sell closes
the whole position, anything else is a hold. Nothing is asked when the answer could not change the outcome (pair already
exiting, risk-blocked, cooling down, no room to enter, missing or stale data: the newest candle must be under 45 minutes old).

Each decision carries `detail` (call id, model, flow result, answer, usage, estimated cost in USD) in `live_decisions`;
`atl-live status` shows it. Hold reasons say what happened: `ai_not_reviewed`, `ai_abstain`, `ai_veto`, `ai_buy_ignored_held`,
`ai_sell_ignored_flat`, `ai_unavailable_<code>` (for example `global_spending_limit`, `paid_spending_disabled`,
`pricing_not_reviewed`, `pricing_not_current`, `failure_breaker`, `no_recent_candles`).

**Money.** Every call reserves its worst case first (6000 input and 400 output tokens by default, about USD 0.02 at
2.5/12 per million) and is refused once the global cap (`ATL_AI_TOTAL_LIMIT_USD`, counted across all envelopes) would be
crossed. Expect about 18 reviews a day at most (3 pairs × 6), far less when the gates skip pairs. Unknown outcomes are
settled at the reserved maximum (DEC-044), so the cap is true and the AI keeps working; after 5 in 24 h it pauses.

**Activation (all four needed, otherwise the AI strategy does not exist and nothing is paid):**
`ATL_AI_ENABLED=true`, `ATL_OPENAI_API_KEY` (a separate key with a hard monthly limit on the VPS), `ATL_AI_TOTAL_LIMIT_USD`
above zero (use 10), and the model's price in `ATL_AI_REVIEWED_PRICES` (verify the current price and model on the provider's
page first; the default model name `gpt-5.6-terra` is from the 2026-10-19 note and must be re-checked). For an unattended run set
`ATL_AI_PRICE_MAX_AGE_DAYS` (for example 14) or refresh the price daily. `atl-live` prints which of these is missing.

**Known limits.** The key is only sent to a public address, but a test transport without a socket cannot show who answered
(it is treated as public). The first real call has not been made: the real response shape, latency and cost are unverified
until slice 5 activation, and the model's quality is exactly what the experiment measures. Two pairs may both be reviewed
(and paid for) when the first one's entry will use up the exposure room; the second buy is then refused deterministically.

Review hardening of slice 3: mechanical exits for the AI portfolio are placed before any model call; a review is stamped
before its call (a crash never pays twice) and unstamped when the question was never put (no recent candles, breaker, local
error), so it is asked again next cycle; a failing AI path is contained per pair; a 200 reply that cannot be decoded and any
provider-claimed "settled" are charged at the reserved maximum; `ATL_AI_PRICE_MAX_AGE_DAYS` works from the environment.
Remaining limits: the destination check resolves DNS separately from the connect (no IP pinning), so a rebinding race could
send the key before the post-connect peer check reports it; non-200 replies (a bad key, rate limiting) are charged at the
maximum and count toward the daily breaker; the model's free-text reason is stored in the journal and must never be fed back
into a prompt.

## 12. Strategy on/off (slice 4)

`atl-live disable <rules|ai|benchmark|strategy-id> --reason "..."` and `atl-live enable ...` switch a strategy; `atl-live audit
[--limit N]` prints the trail, newest first; `atl-live status` shows each strategy's `enabled` flag and last change. The flag
is persisted in `live_strategies.enabled`, read at the start of every cycle (a running loop obeys within one cycle, no
restart, no lock needed), and every change is journaled in `live_strategy_audit` (migration 0014: who, when, on/off, reason)
in the same transaction. Repeating the current state changes and records nothing. The audit actor is `cli:<os user>`; a reason
of 1 to 200 characters is mandatory.

**Switched off means wind-down, not freeze (my design for your review; it replaces the slice-2 behaviour where a disabled
strategy was skipped entirely):** it opens no new positions, makes no model call (so it spends nothing), and journals no hold
rows, but its mechanical exits (stop, take-profit, 72 h, trend break for rules) still run so a switched-off strategy never
leaves a position unmanaged, and its equity keeps being recorded so the curve and the final comparison stay continuous. The
benchmark never sells, so switching it off simply stops its (one-time) entries. Re-enabling resumes normally. The reason shown
in a cycle report is the first gate that applies (`strategy_disabled`, or a risk gate that was already active).

Details from the slice-4 review: the flag is also re-read (one cheap query) before each model call and before each entry order,
so a `disable` issued during a cycle stops the remaining spend and drops a not-yet-sent entry; a planned entry left by a
crash is rejected (`strategy_disabled`) instead of sent when its strategy is off, while a planned exit is still sent; an unknown
strategy id exits with code 2 and `{"status":"failed","error":"unknown strategy"}`. While a strategy is off its data-problem
holds (for example `no_market_data`) are not journaled either; the cycle report's `failures` still lists them. The audit actor
is the OS user and informational only (it can be spoofed with an environment variable); the audit table has no tamper
protection beyond database access control.

## 13. Deployment (slice 5)

Vendor chosen with the user: **Hetzner Cloud, EU**; backups pulled to the user's Mac by hand at least daily. The procedure is
[docs/30-deployment-runbook.md](30-deployment-runbook.md); the files are `compose.prod.yaml` and `deploy/`. `atl-live health`
(exit 0 when any strategy recorded equity within 40 minutes) is the container health check.

Rehearsed locally on 2026-10-26 in an isolated Compose project (own database volume, no published ports, read-only containers
with all capabilities dropped): image build, migrations (exit 0), `atl-live setup`, one real read-only Kraken cycle inside the
container (3 benchmark fills, no errors), `docker compose ps` showing `live` healthy, a backup and a restore test with matching row
counts, then teardown. **Not rehearsed:** the Hetzner server itself, `bootstrap.sh`, the systemd timer and `pull-backup.sh` over
SSH, the AI strategy against the real OpenAI API. The 1-2 week clock starts when the loop first runs on the server.

Review fixes of slice 5: the timer runs as `atl` (backups readable by scp), `bootstrap.sh` installs the timer (no sudo needed), a backup is kept only if it is a readable archive containing our tables, rotation keeps the newest 7 by count, the restore test fails on a bad restore, and the runbook states the limits of `health`, the restart behaviour and that `restore-test.sh` is a manual weekly check. Re-rehearsed locally after these changes (up --build, migrate exit 0, healthy, backup, restore test ok).

## 14. Deployment record (slice 5, done 2026-10-28)

Server: Hetzner Cloud, Helsinki, 2 vCPU class `ubuntu-2gb-hel1`, Ubuntu 24.04.5, name `atl-v0`. Deployed commit `ef92ac59f5a270670f8a91577741928d10b7dd35`
(also in `/opt/atl/DEPLOYED_COMMIT`), image tag `ef92ac5`. Stack as in `compose.prod.yaml`; bootstrap, code and env were placed following
docs/30; the database password was generated on the server and never displayed. AI strategy **off** (`ATL_AI_ENABLED=false`, no key).

Evidence observed on the server: `migrate` exited 0; `live` healthy; the first real cycle at **2026-10-28T16:20:47Z** observed
XBTEUR, ETHEUR and SOLEUR with no errors and filled the benchmark's three paper orders (equity 98.41 after fees and spread); a
backup was taken, its checksum verified and the restore test passed (row counts matched); the backup was pulled to the owner's Mac;
disk 9% used, about 115 MB used by the two containers. **The 1-2 week evaluation clock started with that first cycle.**

Not yet done: AI activation (needs the owner's new OpenAI key with a monthly limit, the verified model and price), a dashboard
(slice 6), alerts (slice 7). Until then the owner checks daily: `dc ps`, `atl-live health`, `atl-live status`, and pulls a backup.

## 15. Error journal (slice 6)

Why: a contained exception used to leave only its type name in the cycle report, so nothing could be diagnosed later. Now every
problem is written to two small tables (migration 0015): `error_groups` (one row per distinct problem: fingerprint of component,
exception type and code location without line numbers; count; first and last seen; strategy and pair; status; redacted message and
stack trace; who acknowledged it and why) and `error_occurrences` (the 20 most recent per problem).

What is recorded: **errors** (an exception in a strategy, the AI path, the paper broker or the worker loop; these start as `new` and
need acknowledging, and reopen if the same problem recurs after being acknowledged) and **warnings** (no exception behind them:
Kraken requests that failed, Kraken data that failed validation, an AI provider that timed out, returned an unusable reply or was
unreachable; always `seen`). Budget and policy refusals of the AI are not outages and are not recorded. Warnings are what the
"Kraken is down" and "AI is down" alerts of slice 8 will read.

Redaction before anything is stored: API keys (`sk-...`), bearer tokens, `password=`, `token:`, `api_key=` style pairs, URLs with
credentials, long hex strings and any 40-character token-like string. Message at most 500 characters, trace at most 6000.

**Storage is bounded** (the owner's concern): one row per problem, at most 500 problems, at most 20 occurrences each, handled or
warning problems expire after 30 days (an unacknowledged error is kept), pruning runs at most hourly inside the loop. Worst case about
15 MB. For scale: measured on the server after three cycles the whole database was 9.6 MB; extrapolated, raw captures and decisions
grow roughly 12 MB a day (about 170 MB in two weeks; an estimate, not a measurement over time), and the disk has 33 GB free.

Recording never raises: if the journal itself cannot write, a one-line notice goes to stderr and the loop carries on.

Reading it: `atl-live errors [--status new] [--level error] [--limit N]` (list and summary), `atl-live errors --show <fingerprint
prefix>` (full detail, trace and recent occurrences), `atl-live ack <fingerprint prefix|all> --reason "..."`; `atl-live status` shows
the summary. The dashboard (slice 7) and the alerts (slice 8) read the same tables.

## 16. Dashboard (slice 7)

Owner decisions: working on/off switches, behind a sign-in; reached through an SSH tunnel; verdict says "too early to tell";
alerts for anything broken (DEC-046). Design iterated in `docs/design/dashboard-v1|v2` with the `dashboard-designer` agent.

What it is: one more container (`dashboard` in `compose.prod.yaml`), the same image, command `atl-dashboard serve`, published on the
server's `127.0.0.1:8000` only. From your Mac: `ssh -L 8000:localhost:8000 atl@SERVER`, then open `http://localhost:8000`.
It has no OpenAI key and no order path. It can read, flip a strategy's flag and acknowledge errors, nothing else.

Sign-in: one owner password. `ATL_DASHBOARD_PASSWORD_HASH` (scrypt, made by `atl-dashboard hash-password`, which asks at a hidden
prompt on the server) and `ATL_DASHBOARD_SESSION_SECRET` (`atl-dashboard make-secret`) live in `/opt/atl/.env.prod`. Unset means every
sign-in is refused. The cookie is signed, HttpOnly, SameSite=Strict, ends after 30 minutes idle (`ATL_DASHBOARD_IDLE_MINUTES`) or 12 hours
in all; writes also need a CSRF header and a same-host Origin; five wrong passwords lock sign-in for 15 minutes (counted globally). A
stolen cookie cannot be revoked before it expires; the tunnel is the real access control.

API (all JSON, `/api/...`, signed-in only): `overview` (everything the first screens need), `decisions` (filtered, paged),
`audit`, `errors`, `errors/{fingerprint}`, POST `errors/{fingerprint}/ack`, POST `strategies/{id}/enabled`, `login`, `logout`, `me`.
A switch calls the same `LiveStore.set_enabled` as the CLI with actor `dashboard:owner` and a required reason; a database failure
returns 503 "nothing was changed". Any unexpected exception in the page's own code is recorded in the error journal as
component `dashboard` and the page shows a plain message.

Alerts (`dashboard/alerts.py`, thresholds in DEC-046) are evaluated live by the overview; slice 8 sends them. Not built yet and marked
as such: backup age (the container cannot see the backup directory by design), strategy consecutive-failure counts beyond the journal's
recent occurrences, sign-in audit.

The page text, the plain-language reason table and the chart are `dashboard/static/{index.html,app.js,app.css}`: no inline script or
style, no external resource (CSP `default-src 'none'; script-src 'self'; style-src 'self'`), all server text inserted as text.

### Deployment record (slice 7, 2026-10-29)

Deployed commit 42d9f15 (backup `atl-20261029T091502Z.dump` taken first). `dashboard`, `live` and `db` healthy; migrate exited 0. The owner
set the password on the server with `/opt/atl/set-dashboard-password.sh` (hidden prompt; hash and session secret written to the 600
env file, never displayed) and signed in through the SSH tunnel. Checked from the server afterwards: three consecutive cycles
(09:15, 09:30, 09:45 UTC) without failures, no unacknowledged errors, the only journal entry is the three Kraken warnings from the
first cycle after the slice 6 restart (a startup hiccup, never repeated). The switch was not exercised by the assistant; the owner tests it from the page.

## 17. Telegram alerts (slice 8)

What it is: one more container, `alerts` (`atl-alerts run`), deliberately **not** part of the loop's process, so it can tell you
when the loop has stopped. Every 5 minutes it computes the same findings the dashboard shows (`DashboardData.overview()` and the
rules R1-R6 of `dashboard/alerts.py`, thresholds in DEC-046) and sends them to one Telegram chat through the existing durable
single-attempt outbox (`notifications/`), so nothing is sent twice by a crash and a message carrying a secret-shaped string is refused.

Behaviour (`alerts/monitor.py`, pure `plan()` plus a runner): a new problem is sent once; the same problem is never repeated within
6 hours (then one "still not fixed" reminder); when it clears you get one "Resolved" note; a message that could not be delivered is
retried on the next check, at most 5 times, then it waits for the next 6 hour window; one problem failing to send never blocks others.
Message text is fixed wording from our own code (rule title, detail sentence, rule id, how to open the dashboard); nothing from the
market, the AI or an exception message is ever copied into it.

Added by the assistant, not requested: a **daily check** message at 07:00 UTC or later (values per strategy, alerts active, errors to
review, AI state). Without it, silence could mean "all fine" or "the alerter is dead". Tell me if you do not want it.

Not covered: the alerter itself dying (Docker restarts it and its healthcheck shows on `docker compose ps`, but nothing outside the
server watches it; the daily check going missing is your signal), and the whole server being down (no alert can come from it; an
external uptime monitor would be a later, separate decision).

Setup (yours, docs/30 section 9c): create the bot with @BotFather, put the token in `/opt/atl/.env.prod`, send your bot a message,
`atl-alerts find-chat` prints your chat id, set `ATL_TELEGRAM_ENABLED=true`, start the container, `atl-alerts test` sends a test
message. Until enabled the container idles and nothing is marked as sent. New table `alert_state` (migration 0016).

