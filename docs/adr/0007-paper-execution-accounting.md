# ADR-0007 — Deterministic fixture execution and recoverable paper accounting

- **Status:** Implemented under approved M3 plan; local verification passed; PR #4 merged as `686f942`
- **Date:** 2026-10-19
- **Authorization:** DEC-032; no approved architectural decision is superseded.

## Decision

Reuse Python/PostgreSQL without another service. The bounded offline `atl-paper`
runner accepts explicitly labeled FIXTURE scenarios only. At the M3 baseline, the paper service also
rejects OBSERVED/REPLAY execution quotes: M2 samples are research evidence, not
qualified execution inputs. No network client, credentials or venue-order interface
exists in this package. Strategies and unattended operation remain later work.

Initial orders are long-only IOC limit orders. The simulator walks explicit book
depth or uses explicit spot capacity/impact assumptions. It never converts aggregate
liquidity to depth. Prices round against the order, quantity rounds down, buy cash
cost rounds up and sell proceeds down, all with explicit instrument quanta. Rounded
cash must still respect the limit price. Fees round up once on aggregate notional.
Fees/cost controls use actual rounded execution costs, including depth/impact and
reference midpoint for books; an absent reference blocks execution. Admission also checks configured fee and spending constraints before simulation.
Effective-cost rejection consumes no liquidity.

Decimal inputs reject floats, nonfinite values and excessive representation/scale;
calculations use a local precision of 100. Fixed-point bounds are engineering
limits, not risk-profile choices. Policy fields and initial funding are required;
fixture examples are nonoperational test assumptions. Budget/exposure caps reserve
acquisition cost at the order limit and aggregate held cost basis, not mark-to-market
NAV or calibrated Conservative/Moderate/Aggressive profiles. Those operational
profiles, loss/drawdown activation and price/fee calibration still require approval.

Each portfolio has one explicit cash unit, separate from its held asset units.
No FX conversion, leverage or shorting is modeled. Gross realized P&L uses weighted
average acquisition cost; a partial sale releases basis rounded down to cash step,
and a final sale releases the remainder. Fees stay separate from cost basis/gross
P&L; fill prices already contain spread/impact, so no second friction deduction is
made. Journals balance independently per unit and include explicit counterparties.
NAV, operating-cost attribution and performance reporting remain later work.

## Durability and recovery

Migration `0003_paper_execution` adds portfolios, immutable commands, orders, fills,
events and journal plus controlled instrument/liquidity projections. A PostgreSQL
portfolio row lock spans reconciliation, admission and all writes. Accepted,
reserved, filled/partial and released events commit with the final IOC result.
There are no resting orders/reservations or background workers. A failed transaction
has no economic effect. A lost response is reconciled by the same portfolio/key;
conflicting intent reuse rejects, with no blind second submission.

Commands retain exact intent/quote provenance, supplied UTC clock and versioned
policy. Recovery replays them from immutable opening funding, then compares every
order/fill, journal line, event, control state, instrument definition, settlement
marker, consumed-depth counter and last event time against the stored projections.
Missing or inconsistent rows block execution; recovery never silently repairs them.
Snapshot capacity is shared between spot buys/sells; prediction depth is per side.
A native instrument cannot change its definition within a portfolio.

The harness is bounded to 100 operations/1 MB per scenario, 1,000 commands per
portfolio and 10,000 rows per materialized projection. Exceeding a history bound
blocks further execution. This prioritizes auditability in M3, without claiming
production ingestion/latency/scale. Database-owner access is not an immutable
security boundary; deliberate coherent rewriting of all history is out of scope.

## Settlement and limitations

Prediction settlement is an explicit per-outcome fixture command. A payout in
[0,1] is supplied in the portfolio cash unit per contract; void refunds remaining
basis, with previously charged trading fees retained. Settlement fee is explicitly
modeled as zero in this initial fixture model. Duplicate resolution keys return
the durable result; conflicting resolutions/settled instrument reentry reject.
Unresolved positions remain open. No automatic adjudication or cross-venue/outcome
inference is implemented; real contract payout/void semantics are not established.

Tests validate engineering invariants, not achievable fills, market access, legal
eligibility or profitability. Real PostgreSQL/container results must be observed
before declaring M3 complete. Shipping and merge remain separately authorized.


M4 extension: [ADR-0008](0008-offline-strategy-research.md) permits immutable,
qualified offline REPLAY registration. Ordinary fixture scenarios/default portfolios
retain this ADR’s restriction; OBSERVED execution and live activity stay disabled.


Amendment (2026-10-25, V0 slice 2, piece A): (1) A `spot` instrument that supplies explicit order-book levels now
fills against that book exactly as a prediction contract does (adverse per-level rounding, fee on notional, the same
round-trip cost gate); without levels it keeps the spot-price/capacity/impact model, and supplying both is still
`ambiguous_quote`. (2) A portfolio created with the immutable markers `live_session_id` and
`live_data_mode=OBSERVED` fills an OBSERVED quote only if the order carries a `KrakenEntry` (the pair-rules, server-time
and book captures) whose derived quote equals the submitted quote; a missing or altered quote is a hard error before any
state changes. This mirrors the runtime's raw-capture rule for the Polymarket worker. (3) `PaperStore.create` accepts
such markers and `submit` accepts the evidence; both are part of the replayed, hash-checked history.
