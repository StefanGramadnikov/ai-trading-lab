# AI Trading Lab

> **Status:** Milestone 3 — deterministic paper execution locally verified; shipping pending.
> **Document version:** 1.2, 2026-10-18.
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
Strategies, AI agents, execution adapters, workers and dashboards remain deferred.
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
