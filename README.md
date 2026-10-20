# AI Trading Lab

> **Status:** Milestone 6 — PR #7 open; checks verified, merge pending.
> **Document version:** 1.4, 2026-10-20.
> **Operating mode:** `PAPER_ONLY` until a separately approved live-trading release.

AI Trading Lab is a private, experimental platform for collecting market data, researching trading strategies, and comparing automated decision-making architectures. It initially targets **Solana memecoins** and **prediction markets** (Polymarket and potentially Kalshi). It is **not** a proven profitable trading strategy.

## Objectives

1. Implement **hybrid AI**, **multi-agent AI**, and **rules-only** approaches using shared market data and comparable simulation assumptions.
2. Test multiple strategy families, including an explicitly labeled **GRONK-inspired** baseline, without accepting unverifiable performance claims.
3. Compare **event-driven** and **periodic/continuous** AI analysis on profitability **after** fees, slippage, AI usage, and infrastructure costs.
4. Start on a Mac, run a **2–4-day local smoke test**, then run cloud paper-trading evaluation for **up to ~30 days** and hold an explicit **GO / EXTEND / STOP** review. Thirty days is a decision checkpoint, **not** proof of an edge.
5. Make the system capable of fully autonomous operation **only within deterministic, tested safeguards**. Real trading remains out of scope for the initial implementation.

## Read this first

| File | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Mandatory coding-agent instructions and safety rules |
| [Requirements](docs/02-requirements.md) | Approved commitments and explicitly unapproved proposals |
| [Architecture](docs/03-system-architecture.md) | Component contracts, data flow and operational boundaries |
| [Experiments](docs/07-experiment-design.md) | Fair comparisons, leakage avoidance and metrics |
| [Testing](docs/08-testing-standards.md) | Every-cycle automated quality gate |
| [Roadmap](docs/10-implementation-roadmap.md) | Milestones, sequencing and acceptance criteria |
| [Decision register](docs/12-decisions.md) | Approved decisions, assumptions and open questions |
| [Progress](docs/13-progress.md) | Current state, blockers and next action |
| [Codex kickoff](docs/14-codex-kickoff.md) | Copy-paste instructions for M1 after M0 review |
| [GRONK source notes](docs/15-gronk-source-review.md) | What the supplied guide actually claims and what remains unverified |

## Core safety rules

- **No live orders, wallet private keys or trading account credentials.** Paper-trading is the only permitted execution mode until the user explicitly authorizes a separate live-trading milestone.
- AI output is **untrusted proposal data**, not an order. Deterministic code owns position sizing, risk checks, reconciliation, accounting and order routing.
- Missing market data, ambiguous market identity or expired prices **block new entries** dependent on that data. Do not hallucinate replacements.
- No auto-deployment of AI-generated strategies or risk-limit modifications.
- Financial performance claims require observed evidence and an explicit accounting definition. **No profit is guaranteed.**

## Local development

Requires Docker with Compose, `make`, and [uv 0.11.7](https://docs.astral.sh/uv/getting-started/installation/).
Python 3.12.13 is pinned in `.python-version`; `uv.lock` pins dependencies.

```bash
uv python install
uv sync --locked
cp .env.example .env
# Inspect the example before using it; it contains local development values only.
docker compose up -d --wait db
uv run --locked alembic upgrade head
docker compose up -d --build --wait api
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

`/health/live` reports process liveness. `/health/ready` returns 200 when PostgreSQL
is reachable and 503 otherwise. It always reports `trading_ready: false`; source
freshness, schema compatibility and trading readiness are outside M1.
Migrations run explicitly, never automatically at API startup.
To run the API directly: `uv run --locked uvicorn ai_trading_lab.app:create_app --factory --reload`.

For checks, create a separate disposable test database and export its URL:

```bash
docker compose exec -T db createdb -U ai_trading_lab ai_trading_lab_test
export ATL_TEST_DATABASE_URL=postgresql+psycopg://ai_trading_lab:local_example_only@localhost:5432/ai_trading_lab_test
make check
uv run --locked pre-commit install
uv run --locked pre-commit run --all-files
```

Run `createdb` once for a new volume. Integration tests migrate **only** a database
named `ai_trading_lab_test`; never use a database containing valuable data.
`make check` does not silently skip integration tests when configuration is missing.
A `.env` file configures the application and Compose; pytest requires the test URL
exported in its process environment. See [testing details](docs/08-testing-standards.md).

Defaults bind API port 8000 and PostgreSQL port 5432 to loopback. If occupied, set
`ATL_API_PORT` and `ATL_POSTGRES_PORT` in the shell or `.env`, and adjust the host
URLs accordingly. Compose builds its internal database URL using `POSTGRES_*`;
host Python commands use `ATL_DATABASE_URL`. Percent-encode URL-special characters
in passwords used in connection URLs. Changing `POSTGRES_*` does not change an
already initialized database volume.

`docker compose stop` stops services while preserving data. `docker compose down`
also removes containers/network while retaining the named volume. No destructive
volume removal is required for normal development.

## Scope boundary

M1 provides the foundation. M2 adds bounded public read-only DexScreener, Solana,
Polymarket and Kalshi adapters, versioned evidence storage and offline replay.
M3 adds paper execution; M4 adds rules-only offline research. AI agents, live execution, workers and dashboards remain deferred.
Only `PAPER_ONLY` is accepted.
The repository is initialized with an `origin` remote; do not run `git init` again.
No financial credentials are needed. Local database example passwords are not
suitable for public deployment. Cloud hosting remains a later milestone.

## Read-only market data (M2)

Apply migrations first. Host CLI uses `ATL_DATABASE_URL` from the environment or
`.env`. No provider keys, wallets or subscriptions are needed for these public
read attempts; availability is not guaranteed. Collection is explicit and bounded:

```bash
uv run --locked atl-data collect --source dexscreener --mint So11111111111111111111111111111111111111112 --limit 1 --capture-file data/dex-sample.json
uv run --locked atl-data collect --source solana --mint So11111111111111111111111111111111111111112 --limit 1
uv run --locked atl-data collect --source kalshi --limit 2 --max-pages 1
uv run --locked atl-data collect --source polymarket --limit 2 --max-pages 1
uv run --locked atl-data replay data/dex-sample.json
```

Create the `data/` directory yourself before using that example capture path;
local captures are ignored by Git. Capture outputs refuse overwrite and are limited
to 8 MB; each source response is limited to 2 MB. Defaults allow 30 attempted
requests, up to 3 attempts per request, a 5-second request deadline and 60-second
collection budget. CLI sampling is capped at 10 observations/mints and 3 pages;
Solana emits separate supply/largest-account observations per mint.

Exit 0 means collection finished without recorded source failures, 1 means failures
were persisted (possibly alongside usable evidence), and 2 means invalid input or
persistence/file failure. Inspect `status`, `mode` and `failure_codes`; zero exit
is not trading readiness. Unknown source time yields PARTIAL; time older than 60s
is STALE, more than 5s ahead is INVALID. Native units and provenance remain explicit.

Replay uses only the archive and PostgreSQL, never public networking. It labels
new evidence REPLAY and retains original receipt times and capture references;
repeating it adds a run but no duplicate capture/snapshot. Archives contain raw
untrusted source text. They are research data, never agent instructions.

The local public smoke persisted DexScreener/Kalshi samples and partial Solana
supply (largest accounts rate-limited). Polymarket returned a transport
`network_error`; public compatibility is unverified here. All four adapters have
offline contract tests. See [M2 plan](docs/18-m2-market-data.md),
[source evidence](docs/16-source-register.md) and [progress](docs/13-progress.md).

## Paper execution fixtures (M3)

M3 is an offline engineering harness; no venue orders or financial credentials.
Apply `uv run --locked alembic upgrade head` to your local development database,
then run the explicit synthetic scenario:

```bash
uv run --locked atl-paper run tests/fixtures/paper_scenario.json
```

It uses the existing `ATL_DATABASE_URL`/PAPER_ONLY configuration. No extra environment
variables or Compose services are needed. The example defines test-only virtual
capital, units, risk caps, fees, capacity and impact; those values are not approved
operational risk profiles or real prices. Only FIXTURE quotes are accepted.

Orders are long-only IOC limits, with deterministic rounding, fees, reservations,
partial fills and cancellation of remaining quantity. Unknown/stale/future quotes,
wrong identities/units and failed admission block fills. Each portfolio/snapshot
consumes finite modeled capacity. Duplicate keys return durable results; changing
an existing intent under the same key rejects. Commands commit independently; a
later scenario failure does not erase earlier committed operations.

The runner reads at most 1 MB/100 operations and always reconciles the final
portfolio. Exit 0 means operations/reconciliation completed, 1 reports expected
order rejection/expiry or unsuccessful reconciliation, and 2 reports invalid input
or persistence failure. For a fresh independent scenario, use a new portfolio ID
and update its intents consistently. Repeating the supplied unchanged scenario
uses its existing portfolio and idempotency keys without adding fills.

[The M3 plan](docs/19-m3-paper-execution.md) and
[ADR-0007](docs/adr/0007-paper-execution-accounting.md) describe accounting, cost and
recovery assumptions. M2 observations do not become executable merely by existing;
realistic execution qualification and operational profile activation remain open.

## Offline strategy research (M4)

Apply migrations, then run an explicitly synthetic research tape:

```bash
uv run --locked atl-backtest run tests/fixtures/research_probability.json
uv run --locked atl-backtest run tests/fixtures/research_momentum.json
```

These scenarios validate engineering, not market performance. They use the same
`ATL_DATABASE_URL` and PAPER_ONLY configuration; no new environment variables,
services or credentials are needed. Each run stores its complete tape, strategy,
policy, decisions and M3 ledger atomically. Repeating unchanged input returns the
verified result; use a new run ID and strategy version for a changed hypothesis.
`code_version` is a declared source commit; `engine_hash` fingerprints the actual
installed Python files, including local changes. Exit 0 means evaluation persisted,
including expected abstentions/rejections; exit 2 means invalid input or persistence
failure. Input is bounded to 2 MB/100 chronological frames.

Reports compare cash on the same funding/window and separate gross realized P&L,
fees and open holdings. NAV/ROI requires qualified full modeled liquidation;
unavailable marks yield null. Operating costs and all-expenses profit are null.
No runtime AI, parameter search or empirical investment result is implemented.

Qualified archived REPLAY requires original source metadata/book captures and exact
native identity, resolution description, times, depth and units. Only Polymarket
books currently meet the structural parser requirements, with explicit generic
`polymarket:unspecified_collateral` units; these are never interpreted as USD/EUR.
This validates archive consistency, not authenticity or calibrated fills. Other M2
sources remain ineligible where time/depth is missing. Ordinary `atl-paper` stays
FIXTURE-only. See [M4 contracts](docs/20-m4-strategies-backtesting.md) and
[ADR-0008](docs/adr/0008-offline-strategy-research.md).

## Paired paper experiments (M6)

Local acceptance: 846 tests, 96.62% coverage, full quality gate, production replay
and independent review passed. Shipping and hosted CI remain pending.

`atl-experiment` binds a complete cohort and source fingerprint before evaluation,
compares one declared variable, and keeps each cell in its own paper ledger. The
supplied example uses synthetic rules/Hybrid/sequential/collaborative evidence,
explicit nonoperational profile numbers, zero-priced fixture AI and a synthetic
shared expense. It never calls OpenAI or another provider, even if local AI is enabled.

With the local database migrated to head:

```bash
uv run --locked atl-experiment create tests/fixtures/experiment_architecture.json --key create --at 2026-10-20T00:00:00Z
uv run --locked atl-experiment start b0000000-0000-4000-8000-000000000001 --key start --at 2026-10-20T00:00:00Z --reason synthetic
uv run --locked atl-experiment run b0000000-0000-4000-8000-000000000001 --key rules --at 2026-10-20T00:00:00Z
uv run --locked atl-experiment run b0000000-0000-4000-8000-000000000001 --key hybrid --at 2026-10-20T00:00:00Z
uv run --locked atl-experiment run b0000000-0000-4000-8000-000000000001 --key sequential --at 2026-10-20T00:00:00Z
uv run --locked atl-experiment run b0000000-0000-4000-8000-000000000001 --key collaborative --at 2026-10-20T00:00:00Z
uv run --locked atl-experiment report b0000000-0000-4000-8000-000000000001
```

Each `run` advances exactly one bounded cell. Retrying its key/time returns the
original snapshot; a new key advances the next cell. `pause`, `resume`, `approve`
and `reject` require `--key`, the original UTC `--at` for retries, and `--reason`.
Pause applies between cells. Risk suspension latches entries off inside a cell
while qualified exits continue, then pauses the cohort. Resume never clears a
completed cell's risk latch. Approval is an evaluation event, never deployment.

Reports include NAV curves through the declared endpoint, matched trading/economic
deltas, profile/AI metrics, exact cost provenance and unavailable reasons. Estimated
costs and invoices remain distinct; linked invoices replace estimates. Shared bills
allocate once and consolidated totals subtract them once. Missing FX/marks/bills or
undeclared expense completeness prevent all-expenses claims and approval. New cohorts
need fresh cell run IDs; cross-runner reuse is refused. These examples establish
engineering behavior, not profitability or real provider performance.

See [M6 plan](docs/23-m6-experiments.md), [ADR-0010](docs/adr/0010-experiment-cohorts-and-economic-accounting.md)
and [progress](docs/13-progress.md) for current verified and pending checks.

## AI development and integration smoke (M5)

Local acceptance passed on 2026-10-20: 689 automated tests and one actual OpenAI
response on synthetic market evidence; conservative estimated cost US$0.0038525.
Exact rerun replayed the stored result without another API request. This verifies
integration, not real-market strategy performance. See [progress](docs/13-progress.md).


Apply migrations, then run deterministic synthetic inference through the same budget,
proposal and paper-ledger path used by real adapters:

```bash
uv run --locked alembic upgrade head
uv run --locked atl-ai doctor
uv run --locked atl-ai run tests/fixtures/ai_hybrid.json
```

Real inference is disabled by default. To perform the separately approved personal
OpenAI integration smoke, configure your ignored local `.env`:

```dotenv
ATL_EXECUTION_MODE=PAPER_ONLY
ATL_AI_ENABLED=true
ATL_OPENAI_API_KEY=replace_locally_with_your_personal_key
```

Never paste keys in chat, commit them or use company billing. Keep your local
`ATL_DATABASE_URL` pointed at the intended development database. After the full
quality gate and review pass, the approved command is:

```bash
uv run --locked atl-ai run tests/fixtures/ai_openai_smoke.json
```

This sends synthetic market evidence to a real OpenAI model, not a venue order.
It validates actual integration, not real-market strategy performance. The fixture
selects `gpt-5.6-terra`, standard service tier and reasoning disabled, with one call,
a fixed US$1 envelope and a conservative maximum reservation of US$0.053248. Rates
were checked on 2026-10-19: US$2 input / US$12 output per million tokens; reservation
uses US$2.50 input to cover the documented cache-write multiplier. Pricing expires
24 hours after its verification time; verify/update it explicitly before new calls
if stale. Do not invent a new envelope to retry an uncertain outcome.

The doctor command prints configuration booleans only. Repeating a completed run
replays the recorded response without another paid call. An uncertain/mismatched
billing outcome holds the reservation and blocks new inference pending investigation;
M5 has no reconciliation override. All API usage costs remain estimates, not invoices.
A valid abstention or risk rejection is a legitimate smoke result; never force a fill.

Hybrid, sequential and bounded collaborative modes share deterministic candidate,
price/risk and accounting controls. Periodic/event triggers process bounded supplied
tapes; no unattended service or deployment is added. OpenAI, xAI and Anthropic have
fixed-origin adapters and synthetic contract tests; only OpenAI spending is approved.
See [M5 plan](docs/21-m5-ai-runtime.md), [provider contracts](docs/22-ai-provider-contracts.md)
and [ADR-0009](docs/adr/0009-ai-runtime-and-cost-reservations.md).

## Development workflow

Implement approved work with tests, relevant integration/contract checks, and the
full quality gate. Confirmed test/review failures enter a bounded fix-and-retest
loop. Substantive work gets an independent AI code review before shipping.

Send `/ship` when the current work is ready to commit, push and open/update its PR.
Until then changes remain local and uncommitted. Selecting the `ship` skill
explicitly is equivalent; `/ship` is a project convention, not a newly installed
native CLI command. Merge/deployment still require separate approval.

See [development workflow](docs/17-development-workflow.md) for roles, authority,
review evidence and repair limits. Python work uses the repository `python-quality`
skill; permanent frontend/design roles are deferred until dashboard work.

## Document conventions

- **APPROVED:** explicitly agreed by the user in the planning conversation.
- **PROPOSED:** a technical recommendation needing validation; may change during implementation review.
- **OPEN:** a decision, integration or fact that requires investigation or approval.
- **BLOCKED:** must not proceed until a named gate is satisfied.

**Authoritative project history:** repository documentation and reviewed Git commits. Chat conversations are helpful discussion context but are not a substitute for a committed specification.
