# 20 — M4 rules-only strategies and offline backtesting

**Authorized:** user approved the presented M4 plan, including controlled qualified
REPLAY input. Baseline: merged M3 PR #4, `686f942`. Branch
`feature/m4-strategies-backtesting`. No shipping, M5 or live execution approval.

## Increments and contracts

1. Immutable versioned strategy/data/run contracts, native identities, exact units,
   evidence references, explicit feature/forecast availability and provenance.
   `TradeProposal | Abstain` remains separate from deterministic order sizing.
2. Validate bounded chronological tapes. Synthetic FIXTURE tapes stay synthetic;
   archived OBSERVED source captures can supply qualified REPLAY quotes only when
   normalized identity, units, price/depth, event/receipt time and provenance agree.
   No missing field is inferred, relabeled or upgraded. Original captures remain.
3. Implement GRONK-inspired momentum/volume-exhaustion and supplied-probability
   mispricing baselines; all thresholds are explicit nonoperational research
   parameters. Missing history, ownership semantics or forecasts cause abstention.
4. Run chronological offline proposals through deterministic sizing and existing M3
   admission/simulation/accounting. No future evidence, look-ahead, repeated depth
   or duplicate orders. Replay registration is immutable per paper portfolio;
   ordinary `atl-paper` scenarios and default portfolios remain FIXTURE-only.
5. Persist immutable strategy versions, research runs and decision evidence using
   migration `0004_strategy_research`. Run identity hashes dataset/config/code/model;
   restart/repeated requests are idempotent and conflicting identities reject.
6. Compare to cash on the same funding/windows; report decisions/abstentions,
   orders/fills/rejections, gross P&L/fees and open holdings. Mark-to-market/ROI is
   null when qualified marks are unavailable. No false realized settlement or
   all-expenses profit, calibrated fills or statistical confidence claim.

## Boundaries and ownership

No AI forecasts/providers, paid data, parameter optimizer, scheduler, live observed
execution, wallets, credentials, FX, shorting/leverage, dashboard or new service.
Real-history empirical validation is pending sufficient qualified data. Fixtures
and negative qualification tests validate engineering, not an investment edge.
The GRONK reference remains untrusted hypotheses; no copied financial limits.

Root owns replay qualification/registration, shared backtest contracts, storage,
runner integration and docs. Narrow strategy and reporting/CLI subtasks may be
delegated with exact interfaces. Independent review uses a fresh read-only context.
Existing M3 commands/config replay behavior stays compatible; old migrations are
never edited. Qualified REPLAY is an explicit M4 extension of ADR-0007, not a live
mode. Entry/exit failures stay recorded, not replaced by assumed fills.

## Verification

- Strategy threshold/equality/missing-data boundaries; forecast/model availability,
  instrument/outcome/currency identity, absence of unique-holder inference.
- Chronological information availability and no future marks/forecasts; immutable
  provenance, fixture/replay separation, no unauthorized quote registration.
- Deterministic sizing, order rejection, failed exits, depth consumption, partial
  fills, replay repeat/restart, dataset/config/code collision and atomic persistence.
- Reporting equations/fees, unresolved/unpriced holdings, cash comparison and
  unavailable NAV/ROI; all original M1–M3 tests remain required.
- Full `make check`, real PostgreSQL/migrations/schema drift, container/CLI/health,
  independent review with at most three repair rounds, docs/progress updates.
- No commit/push/PR until a separate explicit `/ship` invocation.


## Implemented interfaces and qualification limits

`strategies.rules.decide(config, frame, state)` returns a typed proposal/abstention.
`backtesting.runner.evaluate(spec)` produces decisions plus the M3 History;
`ResearchStore.run(spec)` persists/reconciles all research and ledger rows atomically.
`atl-backtest run FILE` uses existing database configuration, with 2 MB/100-frame
bounds. Strategy kind/version binds one immutable config; changed parameters need
a new version. `engine_hash` covers installed Python sources; `code_version` is the
recorded supplied commit, not an attestation that the working tree is clean.

Replay currently qualifies only Polymarket raw metadata/two-sided books with known
source times and nonblank resolution descriptions, preserved original OBSERVED
captures and explicit quanta/assumption version. Collateral remains the explicit
generic `polymarket:unspecified_collateral` unit and is never USD/EUR. Native book
events are unique independently of capture UUIDs. Archive consistency cannot prove
authenticity. Other sources remain blocked on missing source time/depth. Strategy
features and independent forecast/calibration references are supplied research
evidence, not independently verified provider or calibration claims.

M4 local gate: 528 tests and 96.29% coverage, Ruff/mypy PASS. Container production
build, schema/head/drift, health and twice-repeated synthetic CLI examples PASS.
Final independent re-review PASS after resolving two P1 findings; hosted CI requires `/ship`. See progress log
for exact commands, initial failures, repairs and remaining empirical limitations.


## Shipping status

The user invoked the ship skill after implementation approval. M4 was committed as
`bc7568f` and opened in [PR #5](https://github.com/StefanGramadnikov/ai-trading-lab/pull/5).
Independent shipping review and both implementation-head hosted CI runs passed.
The documentation follow-up head is checked on the PR before handoff; no merge,
M5, deployment or live execution approval is included.
