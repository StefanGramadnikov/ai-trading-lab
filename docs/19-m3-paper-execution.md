# 19 — M3 deterministic paper execution

**Authorized:** user “execute the next phase”, following the M3 plan in chat.
**Baseline:** M2 merged PR #3, `d52da05`. Branch `feature/m3-paper-execution`.
Implementation approval does not authorize staging, commits, pushes or PR changes;
those require a new `/ship` invocation. M4 and live execution remain unauthorized.

## Contract and increments

1. Immutable, validated paper-only order, policy, quote and settlement contracts;
   explicit units/rounding, injected UTC clock, versioned simulation assumptions.
2. Per-portfolio virtual funding, cash/quantity reservations, long holdings,
   weighted-average cost basis, gross realized P&L and separately recorded fees.
   Balanced journals per asset/unit; amounts never use float arithmetic.
3. Deterministic admission: exact identity and units, as-of freshness/availability,
   expiry, pause state, balances and explicitly configured cost/exposure limits.
   No activation of unapproved Conservative/Moderate/Aggressive numbers.
4. IOC limit-order simulation. Walk bounded book depth or use explicit spot quote
   capacity/impact assumptions. Track consumed liquidity per portfolio/snapshot;
   no invented depth from DexScreener aggregate liquidity. Fixed injected latency,
   no randomness, no network I/O. Partial fills cancel the remaining quantity.
5. PostgreSQL transaction/portfolio lock covers admission, reservation, fill,
   release, journal, order events and state projection. Accepted/reserved events
   are durable with the final result in the same transaction; IOC introduces no
   resting/background orders. Same portfolio/key/payload returns the durable result;
   key reuse with different payload rejects. Failed transactions leave no effects.
6. Explicit fixture-driven prediction resolution (win/loss/void), duplicate
   settlement protection, restart reconciliation from journals and order records,
   and a bounded offline scenario CLI. Unresolved holdings remain open.

## Interfaces and ownership

`paper_execution/models.py` owns immutable typed contracts; `simulation.py` owns
pure bounded fill calculation and time/quote validation. Root owns portfolio/ledger
accounting, persistence/migration/service and tests. A separate narrow CLI subtask
may start after those interfaces settle. Independent review uses a fresh context.

Persistence adds migration `0003_paper_execution`, never edits applied migrations.
Use existing SQLAlchemy metadata/database/tooling and API+PostgreSQL topology.
No strategies, AI, exchange execution, credentials, scheduler, shorting, leverage,
automatic FX or deployment. Each portfolio has one explicit cash unit; fees and
settlement payouts use that same unit. Different currencies stay separate.

## Acceptance and verification

- Exact money/unit/quantity rounding; fee reservation and release; balanced journals,
  nonnegative available balances/holdings, position cost/P&L and no double-counted
  friction. Symbol/title cannot establish identity or collateral equivalence.
- Limit/expiry/latency, partial/no fills, unknown/stale/future data, admission limits,
  pause, missing assumptions and consumed depth reuse, no future-data leakage.
- Duplicate and conflicting keys, concurrent spending/selling, atomic rollback,
  restart/retry after lost response, projection corruption and settlement repeats.
- Unit, real-PostgreSQL integration and offline scenario tests; all existing tests;
  `make check`, migration upgrade/downgrade/upgrade, metadata drift, production
  container/CLI/health, Markdown links and `git diff --check`.
- Independent review and at most three automatic repair rounds. Report actual
  outcomes in document 13. Hosted CI is NOT RUN until separately authorized ship.

M2 public observations with unknown event time, missing depth/collateral or stale
prices are rejected, not upgraded to executable quotes. Fully qualified synthetic
fixtures validate engineering behavior; no fill realism/profitability is claimed.


## Review repair and current verification

Independent review round 1 found nominal cost checks missing rounded/depth costs,
and incomplete recovery checks for missing orders/control state/consumption.
The repair computes effective rounded cost and replays authoritative commands,
comparing all stored projections before allowing another operation. See ADR-0007.
The initial PostgreSQL/full-gate blocker was resolved after retrying the local
runtime. Final full gate: 410 tests, 95.57% coverage, Ruff/mypy PASS. Migration
round trips/schema consistency, production container/health and twice-run fixture
CLI checks PASS. Document 13 records exact commands and historical incident.
Hosted CI remains pending explicit /ship; no commit/push/PR or M4 work is authorized.
