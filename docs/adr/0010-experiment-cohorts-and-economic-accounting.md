# ADR-0010 — Bounded paired experiments and explicit economic evidence

- **Status:** Approved M6 plan implemented; local gate, production checks and independent review passed; shipping pending
- **Date:** 2026-10-20
- **Authorization:** DEC-036, document 23; no recurring API activation

## Decision

Extend the modular monolith with `experiments/`, the existing PostgreSQL metadata,
new migrations 0007/0008 and an explicit CLI. No service, dependency, background scheduler,
UI or real execution adapter is added. M6 runs bounded supplied tapes (at most
16 cells, 100 frames per cell) and fixture inference only. The M5 personal-key smoke
is development evidence and cannot be imported as a matched experimental cell.

Migration 0008 adds a shared run-ownership registry, preserving the initially applied 0007 lifecycle migration. M4/M5 and M6 use the
same run locks and immutable ownership/configuration checks. Existing legacy
research, AI and experiment records reserve their namespaces even without a
registry row. M4 claims roll back with failed pure research persistence; AI and
experiment identity commits before any provider work. New cohorts require fresh
cell run IDs, preventing cross-experiment cost aggregation or replay invalidation.

Experiment configuration, complete cell definitions and installed-source fingerprint
commit before running a cell. One transaction/advisory lock serializes lifecycle
commands and each bounded cell; its paper projections/report/checkpoint commit
atomically. Provider call reservations/results commit separately through M5, so an
interrupted cell reconstructs from recorded answers under exactly the original
configuration. Ordinary M3 portfolio mutation remains prohibited. Event hash chains,
checkpoint hashes and complete reconciled projections detect missing/altered rows;
a database owner rewriting all records coherently is outside this boundary.

Lifecycle: CREATED→RUNNING→EVALUATING→APPROVED/REJECTED, with explicit pause/resume.
Commands have immutable idempotency keys, payloads including UTC timestamp, original
snapshots and monotonic accepted times. Retrying a key requires the original time.
Manual pause takes effect between cells; this is not a deployed emergency stop.
Approval requires configured sample/valuation/cost completeness and a human CLI
command; it never deploys a strategy or enables live trading. A synthetic evaluation
can be approved as engineering evidence without becoming empirical market evidence.

## Comparison, risk and valuation

Freeze a common dataset/window/strategy/capital/simulation. Declare exactly one
comparison dimension: architecture, activation, provider or risk. Differences in
other configuration fields reject. Separate run and portfolio identities avoid
shared virtual capital or copied fills. Fixture answer sequences are outcomes,
not proof of independent forecasts or real provider differences.

Each named paper profile explicitly supplies its own fractions; no numeric defaults
or operational limits are approved. Order, position and total exposure caps use
opening virtual cash and cannot widen original M3 caps. Daily loss is realized
gross less paid fees relative to that execution/valuation UTC day's opening value (including simulated latency), divided by initial
cash. Drawdown uses peak qualified full-liquidation NAV. Equality breaches; missing
NAV suspends conservatively. Breaches latch entries/inference off for the rest of
the tape, while deterministic exits/marking continue. The cohort pauses after the
cell. Resume permits the next cell/evaluation, never clears that cell's risk latch.

NAV marking reuses M4's depth/fee/latency model, including a terminal valuation at
the declared window endpoint; stale terminal marks cannot use earlier NAV. Public
reports expose the full curve and recorded AI traces, not only summary statistics. End/open positions are not realized
settlements; unavailable marks remain unavailable. Metrics preserve all timestamps,
including simultaneous frames; volatility is unavailable for zero/irregular sampling
intervals or insufficient data and never annualized automatically. Trade matching,
calibration, statistical significance and continuous-coverage claims are unavailable
until their required evidence/methodology exists.

## Costs and interpretation

M5's verified call ledger supplies per-run estimates; uncertain holds are not
settled cost or zero. Expense identities bind one economic item. An invoice may
replace its linked estimate, preserving category and allocation; it is not added
on top. Direct expenses use weight one; explicit shared weights sum exactly one.
Each effective bill is counted once in consolidated expense totals. Declared
complete expense coverage is required for all-expenses economic results.

Currencies require explicit units and dated FX provenance; no inferred inverse,
interpolation, collateral equivalence or live FX request. Report estimate/actual
subtotals, allocation and original evidence separately. Synthetic FX/cost evidence
stays labeled. Trading P&L already contains fill friction and paid fees, so economic
P&L subtracts only operating expenses. No VAT/tax assumption, subscription purchase,
invoice authenticity or economic profitability is inferred from a green test.

## Consequences

Fixture acceptance can verify deterministic matching, accounting and recovery now.
Actual paired market tapes, calibrated forecasts, numeric operational profiles,
recurring provider budgets and sufficient resolved outcomes remain separate gates.
M7 reporting, M8 deployment and any live pilot still need approval.
