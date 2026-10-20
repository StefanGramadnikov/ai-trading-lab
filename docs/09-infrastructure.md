# 09 — Infrastructure, deployment, security and operations

**Status:** Local→cloud, dashboard+Telegram, start-simple APPROVED; exact vendors and commands PROPOSED. **Date:** 2026-10-17.

## Target environments

### Local Mac (M1–M8 development)

- Private Git repo and Codex IDE/CLI; Python pinned in project metadata; Docker Desktop or a supported Docker-compatible runtime.
- M1 Compose services: FastAPI API and PostgreSQL 17.9. Worker/scheduler and dashboard are deferred until needed, per approved M1 plan. No message broker initially.
- Local `.env` created from committed `.env.example`, credentials **not committed**.
- DB migrations versioned from inception; seed fixtures sanitized and deterministic.
- Health endpoint should distinguish process alive, data quality and trading readiness; a running web server does **not** imply market data is healthy.
- To run 2–4 unattended days, Mac power settings must prevent sleep and maintain stable network; record interruptions.

### Cloud VPS (after local smoke pass)

- Linux VPS capable of running pinned container images with persistent storage, a backup and restore test, memory/disk observability, log rotation and restart policies.
- External dashboard via authenticated HTTPS endpoint or VPN; database and admin API not publicly exposed by default.
- Deploy **known Git commit** and signed/tagged container image or equivalent digest; preserve migrations and rollback plan.
- Restore/reconcile outstanding simulated orders and experiment state after restart; avoid rerunning historical inputs as new trades.
- Costs and uptime measured per period and attributed transparently across experiments.

## Monitoring and alerting

Dashboard: net and gross returns, unresolved exposure, provider costs, health, source freshness, run status, rules triggered, rejections, fills and simulated slippage. Telegram: critical failure, safety halt, budget warning, daily summary; no bot-control interface initially.

## Deployment gates

1. M1 CI and local tests pass.
2. Market adapter contract tests and paper-simulator invariants pass.
3. 2–4 local smoke days recorded: uptime, errors, restart recovery, cost checks and stale-feed behavior.
4. Repeatable cloud deployment using same code and config version; DB backup/restore tested.
5. Remote access security and permissions checked before public exposure.
6. Only paper-trading workloads run; live capability stays disabled by separate gate.

## Data retention and security

- Keep immutable metadata sufficient to replay an experiment: hashes, original observations, receipt times, schemas, model/prompt identifiers, trade-intent/ledger events, price-model versions and calculations.
- Establish bounded retention/tiering for high-rate raw data before running for 30 days. Avoid putting raw/high-volume datasets directly in Git.
- Do not log full access tokens, model auth headers, session cookies, wallet secrets, personal customer data or unnecessary account identifiers.
- Sanitized contract-test fixtures may live under `tests/fixtures` once M1 starts.
- Do not rely on public Telegram messages as the only operational record.

## Availability caveats / open checks

- Confirm read-only API availability, authentication, rate limits and terms of use for DexScreener, Solana RPC, Polymarket, Kalshi and optional context providers.
- A prediction market may be visible over HTTP yet unavailable for legal/commercial trading in the user's jurisdiction. Assess separately before live implementation.
- Source uptime and on-chain/pool coverage are not guaranteed; adapters need explicit degraded states.
- Final VPS provider, plan, currency, storage limits and budget approvals are OPEN.

## Disaster/recovery runbook outline (expand at M1–M8)

1. Enter safe degraded mode; block new simulated exposure.
2. Preserve last committed event/offset; snapshot logs and configs securely.
3. Restore connectivity/storage; replay deduplicated data and reconcile ledger.
4. Verify invariants and data freshness before reactivating entries.
5. Log incident, affected experiments, any accounting correction and follow-up test.

## M1 implementation details

- Python 3.12.13 and uv 0.11.7; dependencies locked; production container runs as UID 10001.
- Compose exposes API/database only on loopback, stores PostgreSQL in a named volume,
  waits for database health, and probes API readiness. No broker or remote services.
- SQLAlchemy async engine + psycopg; bounded readiness probe and engine disposal on shutdown.
- Alembic baseline `0001_foundation` initializes migration tracking without speculative
  domain tables. Run `uv run --locked alembic upgrade head` on the host or
  `docker compose exec -T api /app/.venv/bin/alembic upgrade head` in the container.
- Liveness proves process health only. Readiness checks connectivity, not migration
  compatibility, market data quality or trading readiness; `trading_ready` is false.
- `.env.example` contains local placeholders. No provider or financial keys are required.
- Source installation and operating commands are in README; hosted deployment remains gated.


## M2 local data operations — 2026-10-18

M2 reuses API + PostgreSQL; no scheduler or extra service is added. Apply
`uv run --locked alembic upgrade head` to the intended development database before
`uv run --locked atl-data collect ...` or `replay ...`. Migration `0002_market_data`
adds the evidence tables; `0001_foundation` remains unchanged. Host CLI uses
`ATL_DATABASE_URL`; container commands use the Compose internal URL.

Capture archives are opt-in local files, bounded to 8 MB and never overwritten.
The optional `data/` path is Git-ignored; no retention daemon is configured.
ObservationStore enforces append-only/idempotent writes; database owner access is
not an immutable security boundary. Backup/retention and restrictive deployment
roles still belong to deployment hardening. Public-source outages and rate limits
are persisted as ingestion failures; no paid/authenticated fallback exists.


## M3 fixture operations — 2026-10-19

M3 uses the same API/PostgreSQL services and `ATL_DATABASE_URL`; no additional
credentials or environment variables. Explicitly apply migration head, then run
`uv run --locked atl-paper run tests/fixtures/paper_scenario.json` on the host.
Production images package the CLI, not test fixtures. For a controlled container
smoke use `docker compose exec -T api /app/.venv/bin/atl-paper run /dev/stdin < tests/fixtures/paper_scenario.json`.
The input remains bounded and FIXTURE-only; no volume or network service is added.

Run ID/portfolio/command keys are explicit: repeated identical scenarios return
durable results rather than creating new funding/fills. The bounded command replay
checks all projections before further execution; corruption blocks operation and
requires investigation, never silent correction. The initial 1000-command history
limit is for engineering scenarios and does not establish production scale.


## M7 local reporting and notification operations

The existing API serves `/dashboard` and `/api/reports/*`; no new service or broker.
Reporting credentials are local environment secrets and never JavaScript storage.
Both username and password are required; blank disables reporting while health
contracts remain available. Compose publishes only loopback. HTTP Basic here is
local only; authenticated HTTPS/VPN and deployment hardening remain M8 gates.

The additive `0009_notifications` migration creates a durable outbound outbox.
Queue/inspect/send/recover commands are explicit, bounded and have no schedule.
A claimed delivery cannot resend after crash or uncertain response; terminal
rejections are not automatically retried either. This prioritizes avoiding
duplicate/ambiguous sends over guaranteed delivery. Delivery failure cannot stop
deterministic risk controls. `finished_at_utc` is the supplied command timestamp,
not a measured network-completion time. Recipient identity is stored as a hash.

Telegram bot token and numeric chat ID stay in ignored local environment. Do not
include credentials in commands or output; real delivery requires separate user
approval. Development/CI only use injected HTTP transports. Daily summary is an
explicit cumulative snapshot for a UTC date, not independently computed daily P&L.
No always-on alerting or database-outage delivery guarantee is established in M7.
