# 03 — System architecture

**Architecture:** approved high-level design; interface details PROPOSED. **Date:** 2026-10-17.

## Decision

Implement a **modular monolith** with separate runtime workers as needed, shared Postgres persistence and a configuration-driven experiment manager. Same code and Compose definitions run locally on Mac and later on VPS. Avoid microservices, Redis or queues without a measured need.

## Logical flow

```text
External sources (DexScreener, Solana RPC, Polymarket, Kalshi, later news)
      ↓ bounded collectors + raw provenance + schema validation
Normalized market data & append-only observation store
      ↓ common immutable event/snapshot identifiers
Experiment scheduler → Strategy runners:
      ├── Rules-only baseline
      ├── Hybrid AI (periodic OR event-driven)
      └── Multi-agent (sequential OR bounded collaborative)
      ↓ typed trade proposals, never raw executable orders
Deterministic risk engine (limits, portfolio invariants, stale-data gates)
      ↓ validated order intent / reject reason
Paper execution simulator (M0–M8) → positions / fills / accounting ledger
      ↓
Metrics + cost attribution + dashboard + Telegram + immutable run history
```

## Component contracts (PROPOSED)

### Source adapters

- `MarketDataAdapter`: `fetch_markets`, `fetch_observations`, `health`, `rate_limit_status`; normalize ids, decimals, venue, timestamps, price units, fee units and liquidity.
- `DiscoveryAdapter`: identify new token/pair candidates from an explicit source. Token search results are **not** equivalent to an exhaustive list of launches.
- `ProviderCapability`: distinguish market discovery, order-book reads, historical reads, read-only access, and possible **future** authenticated execution.
- `Observation`: unique source/venue/instrument ID, `event_time_utc`, `received_at_utc`, payload fingerprint, schema version, freshness and source provenance.
- `DataQuality`: VALID | STALE | PARTIAL | INVALID; never turn missing numbers into zero without an explicit reason.

### Strategies and AI

- `Strategy.evaluate(snapshot, portfolio, config) -> TradeProposal | Abstain`: proposals contain instrument identity, direction, rationale/evidence references, validity window, strategy/prompt/model versions and price assumptions.
- `AIProvider.analyze(request) -> ValidatedResponse`: response schema validation, token usage, pricing snapshot, latency, tool-call counts, failure/timeout reasons.
- `AgentOrchestrator`: bounded graph/sequential stages, error states, maximum rounds/time/tokens and explicit audit trail.
- External model output can include adversarial text: never give an AI the ability to execute shell commands, connect to live brokers, change risk configuration or deploy production code through trading-time tools.

### Risk / execution

- `RiskEngine.evaluate(intent, snapshot, state) -> APPROVE | REJECT(reason)`, implemented in code independent from AI.
- `OrderIntent`: immutable, idempotency key; includes experiment, strategy, market version, quote time and expected worst-case cost.
- `PaperExecutionAdapter`: simulated accepted/rejected/partial/fill/failed lifecycle with configurable latency, spread, fee and slippage models; publishes to durable ledger.
- `PortfolioLedger`: cash, reserved cash, liabilities, open notional, realized and unrealized P&L, fee ledger; balances reconcile at restart.
- Live adapter interface may be specified but **must not make live requests or carry credentials** before separate approval.

### Experiments

- `ExperimentConfig` immutable once a run starts; content-addressed or versioned, with Git commit hash, market source version, pricing model, prompt version, model ID, risk policy and initial virtual capital.
- Scheduler creates deterministic observation sets for replay and time-aligned live trials. Distinguish tick/market sampling from periodic AI activation.
- Persist successful AI calls and failures; account for failures that incur cost, where provider bills such calls.

## Data model (PROPOSED)

Core tables: `markets`, `market_snapshots`, `raw_source_events`, `strategies`, `strategy_versions`, `experiment_configs`, `experiment_runs`, `proposals`, `ai_decisions`, `orders`, `fills`, `positions`, `ledger_entries`, `portfolio_snapshots`, `risk_events`, `api_usage`, `system_events`, `experiment_evaluations`.

Required keys/invariants:

- IDs are scoped by source/venue/instrument and native contract address or market ID; **never match only by ticker or title**.
- Append-only observations/ledger entries; modifications use corrections with provenance.
- Unique idempotency key per attempted order and experiment; duplicate deliveries are harmless.
- Monetary types use `Decimal`/PostgreSQL `NUMERIC`; base/quote currency tracked; never silently mix EUR, USD and token quantity.
- All time values timezone-aware UTC, retaining source timestamps and time received.
- Never report final realized P&L for unresolved outcome contracts; mark open liabilities and settlement states.

## API transport and reliability

- HTTP clients must set timeouts, bounded retries, per-source rate limits, circuit breaker or equivalent degraded state, schema validation, telemetry and user-agent identification.
- Failed client calls **do not imply no market activity**. Missing data should propagate into decision quality gates.
- Market data acquisition continues even if AI provider is degraded, but dependent strategies must abstain.
- Deterministic risk and account reconciliation run independently of AI evaluation queues; UI failures must not interrupt risk monitoring.

## Local / cloud topology

**M1 implemented Compose services:** app/API and PostgreSQL. Worker/scheduler and dashboard remain deferred until their milestones, per approved M1 plan. Start with one Python package and only split worker processes where needed. Backend read-only dashboard access by default; remote dashboard requires authentication/HTTPS, no publicly exposed database ports.

Mac local 2–4-day unattended smoke test; then pinned image/code commit to VPS for up to ~30-day cloud evaluation. Do not count development-only pre-comparison trades in a paired architecture comparison.

## Threats to correctness

- Survivorship bias from only inspecting tokens that still exist; obtain true launch-event history where feasible.
- Prediction question mismatches across venues; exact resolution terms and unique outcome tokens required.
- Order book / liquidity changes between observation and decision.
- Replayed models inadvertently seeing future context; prompts must use only archived contemporaneous data.
- Duplicate orders after timeouts, lost accounting state on restart, leaked credentials, unforeseen API costs.

See [risk](06-risk-management.md), [experiment design](07-experiment-design.md), [testing](08-testing-standards.md).


## Implemented M2 boundary — 2026-10-18

[Document 18](18-m2-market-data.md) and [ADR-0006](adr/0006-market-data-evidence.md)
record concrete source interfaces. `Transport.request(...) -> Capture` is injected
into four adapters, each returning `Batch(captures, observations, failures)`.
`HttpTransport.health` exposes per-source degraded/rate-limit state for the bounded
invocation; it is not a background monitor or trading-readiness endpoint.
`ObservationStore.save(source, batch)` atomically persists versioned `markets`,
`raw_source_events`, `market_snapshots` and `ingestion_runs`. The CLI is the only
collection entry point. FastAPI health endpoints retain their M1 contracts.
Future strategy/risk/accounting interfaces above remain proposed, unimplemented.


## M3 paper boundary — locally verified

The offline fixture runner submits immutable intents/quotes to `PaperStore`.
`History` replays durable commands through deterministic admission, simulation and
accounting to verify all projections before another operation. PostgreSQL portfolio
locks serialize spending/selling and idempotent results. Accepted/reserved/fill/
release events commit atomically; IOC leaves no resting orders. No network client
or venue execution adapter is introduced. [ADR-0007](adr/0007-paper-execution-accounting.md)
records explicit cost-basis, rounding, fixture-only and bounded recovery semantics.


## M4 offline research boundary

Versioned rules produce proposals/abstentions from immutable chronological tapes;
a separate runner owns deterministic sizing and routes every intent through the
existing M3 History engine. ResearchStore atomically persists the entire run and
ledger. Default paper scenarios stay FIXTURE-only; ADR-0008 describes the bounded,
raw-capture-derived REPLAY extension. No AI, worker or live adapter is introduced.


## M6 experiment boundary

`ExperimentStore` durably binds full cohort/source identity before cell execution,
serializes explicit commands and atomically commits cell reports/paper projections.
The runner reuses M3 sizing/risk, M4 marking and M5 bounded inference reservations.
Pure metrics/cost modules produce paired reports with missing evidence preserved.
Manual pause applies between bounded supplied-tape cells; this is not an unattended
scheduler or deployed safety controller. See document 23 and ADR-0010.


## M7 local reporting boundary

The existing FastAPI process serves authenticated read-only reporting routes and
packaged static dashboard assets. Reporting reuses M6 replay checks in a read-only
repeatable snapshot; historical runtime mismatches refuse financial detail/export.
The PostgreSQL notification outbox has explicit bounded CLI preparation/delivery,
no background worker. It cannot control experiments or trading. See document 24.
