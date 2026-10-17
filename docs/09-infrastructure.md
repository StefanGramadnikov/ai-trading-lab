# 09 — Infrastructure, deployment, security and operations

**Status:** Local→cloud, dashboard+Telegram, start-simple APPROVED; exact vendors and commands PROPOSED. **Date:** 2026-10-17.

## Target environments

### Local Mac (M1–M8 development)

- Private Git repo and Codex IDE/CLI; Python pinned in project metadata; Docker Desktop or a supported Docker-compatible runtime.
- Compose services **proposed:** FastAPI app, background worker/scheduler, PostgreSQL, Streamlit dashboard. No message broker initially.
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
