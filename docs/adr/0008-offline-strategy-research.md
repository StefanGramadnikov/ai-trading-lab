# ADR-0008 — Rules-only offline research and qualified archive replay

- **Status:** Implemented under user-approved DEC-033; local checks and independent re-review PASS; shipping authorized; merge pending
- **Date:** 2026-10-19
- **Extends:** ADR-0007's fixture-only restriction solely for registered offline REPLAY

## Decision

Keep the modular monolith, existing Python dependencies, PostgreSQL and M3 engine.
`atl-backtest run FILE` evaluates a bounded immutable chronological tape. Rules
return proposals or explicit abstentions; a separate runner rounds limits inward,
sizes buys from an explicit research notional and sells only existing holdings.
M3 admission, fee/cost limits, IOC simulation, finite depth and accounting remain
mandatory. Policies and strategy thresholds are explicit test/research parameters,
not approved operational risk profiles. There are no financial or network clients.

Strategies are momentum in young pools with volume-exhaustion exits, supplied
independent-probability mispricing and cash. Pool age is never token-launch age.
Ownership screening is optional but, when enabled, requires explicitly supplied
unique-owner balance semantics; token-account concentration is insufficient.
Prediction forecasts require native outcome identity, resolution-description hash,
produced/available times and provenance/version/calibration references. Recording
these references does not establish independence, calibration or authenticity.

## Data qualification

FIXTURE tapes retain synthetic labels. REPLAY portfolios register an immutable
manifest with original OBSERVED metadata/book captures and explicit assumption
versions. Quotes are derived from raw bodies with the existing M2 parser, rather
than trusting supplied normalized observations. Only the Polymarket book currently
provides sufficient source timestamps and explicit two-sided depth. Match the exact
native token, market, outcome and nonblank resolution description. Metadata must
have arrived by book receipt. Capture paths are restricted by the M2 read allowlist.
Original captures and their original modes remain in the persisted tape.

The book does not establish a fiat collateral denomination. Use only the explicit
generic unit `polymarket:unspecified_collateral`, with base unit `contracts`; never
fund or compare it as USD/EUR. Tick/quantity/cash quanta and fees remain supplied
simulation assumptions. Other M2 source observations lack execution-critical time
or depth and remain ineligible. Archive consistency cannot attest that a supplied
archive really came from a provider. No genuine historical performance validation
has occurred; synthetic test archives are engineering evidence only.

Every decision uses only source/receipt/feature/forecast times available at its
virtual clock. Future evidence causes abstention; missing values are not inferred.
Frames are chronological within the declared window, including configured IOC
latency. Quote definitions and finite depth cannot be reset by repeating evidence.

## Persistence and reproducibility

Migration `0004_strategy_research` adds strategy versions, research runs and every
decision/receipt. Freeze inputs, parameters, raw evidence, window and declared source
commit. Also hash the installed Python sources so a dirty working tree is not
mistaken for the declared commit alone. Content hashes bind data/config/results.
All research rows, funding, M3 commands, fills, events, journals and projections
commit in one transaction. Concurrent/restarted identical requests reuse the result;
conflicting identities and corrupt/missing projections reject. Completed research
portfolios cannot be extended through ordinary paper mutation commands.

Limits: 2 MB CLI input, 100 frames, 100 history points per frame; replay manifest
at most 100 entries/2 MB, each original response at most 100 kB. These engineering
bounds do not establish production throughput. No new scheduler or service is added.

## Reporting limits

Compare cash under the same funding and window, with zero assumed cash interest.
Record proposals, abstentions, sizing refusals, orders/fills/rejections, gross realized
P&L and paid fees separately. Open holdings remain open; no implicit settlement.
End-window NAV uses a complete modeled liquidation from qualified remaining depth,
including latency, fees and cost caps. Missing/stale/future/partial/unfillable marks
make NAV and ROI null. Marks never consume real ledger depth or realize gains.
Gross minus all paid fees includes entry fees on open positions; it is not labeled
completed-trade net profit. Operating expenses and all-expenses profit remain null.
M6 allocation/cohort metrics and M5 AI are deferred. No profitability claim follows
from successful engineering fixtures.
