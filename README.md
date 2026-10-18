# AI Trading Lab

> **Status:** Milestone 1 — foundation complete and merged in PR #1; M2 awaits approval.
> **Document version:** 1.1, 2026-10-17.
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

- **No live orders, no wallet private keys, no trading account credentials in M0 or M1.** Paper-trading is the only permitted execution mode until the user explicitly authorizes a separate live-trading milestone.
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

M1 contains the API, configuration, PostgreSQL connectivity, migration baseline,
tests and CI definition. There are no market integrations, strategies, AI agents,
execution adapters, workers or dashboard yet. Only `PAPER_ONLY` is accepted.
The repository is initialized with an `origin` remote; do not run `git init` again.
No financial credentials are needed. Local database example passwords are not
suitable for public deployment. Cloud hosting remains a later milestone.

## Document conventions

- **APPROVED:** explicitly agreed by the user in the planning conversation.
- **PROPOSED:** a technical recommendation needing validation; may change during implementation review.
- **OPEN:** a decision, integration or fact that requires investigation or approval.
- **BLOCKED:** must not proceed until a named gate is satisfied.

**Authoritative project history:** repository documentation and reviewed Git commits. Chat conversations are helpful discussion context but are not a substitute for a committed specification.
