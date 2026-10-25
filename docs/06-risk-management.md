# 06 — Risk management and live-trading gates

**Status:** Risk-profile comparisons APPROVED; numeric limits PROPOSED and **not authorized** as live limits. **Date:** 2026-10-17.

## Principles

- Paper portfolios simulate capital but still obey deterministic safety checks for reproducibility.
- Future live trading is **separately gated**; current implementation must remain `PAPER_ONLY` and contain no funded credentials.
- The worst-case loss of a memecoin position may approach **100% of its notional**, and exits can fail. An apparent stop-loss price is not a guarantee.
- Prediction-market contracts can lose the full stake, lock capital until settlement, or trade without sufficient exit liquidity.
- Decisions to **open** positions fail closed when data or risk checks are uncertain. Emergency **close** logic must consider whether execution is actually possible and avoid blind retries.

## Risk dimensions and profiles

The user approved comparing **Conservative**, **Moderate**, and **Aggressive** profiles during paper trading, then potentially **Moderate** for an authorized live pilot. The following prior planning table is an *unapproved illustrative starting point*, not validated policy:

| Parameter | Conservative example | Moderate example | Aggressive example |
| --- | ---: | ---: | ---: |
| Maximum position allocation | 2% | 5% | 10% |
| Maximum open exposure | 10% | 20% | 40% |
| Daily realized-loss suspension | 2% | 3% | 5% |
| Peak-to-trough drawdown suspension | 5% | 10% | 20% |

**OPEN before activation:** define precisely whether each threshold applies to initial virtual equity, current NAV, available cash, realized P&L or mark-to-market P&L; model correlation/concentration; clarify aggregation across strategies and venues. Allocation ≠ expected stop-loss risk.

## Mandatory deterministic controls

1. **Pre-trade:** instrument validity, permissible market, source freshness, exact outcome identity, current exposure, available balance, concentration/liquidity, estimated max round-trip cost, duplicate intent, per-market limits, global risk state and budget state.
2. **Portfolio:** reservations for open orders, total/notional exposure, per-token/per-event concentration, leverage prohibition by default, currency conversion, loss and drawdown tracking including unrealized losses, independent virtual ledgers per experiment.
3. **Simulation:** partial fills, spreads, fees, impact, expired orders, unavailable exits, settlement, and orders with unknown outcome. Document conservative assumptions and uncertainty intervals.
4. **Operational:** heartbeat, stale feed, provider outages, disk full, clock skew, process restarts, kill switch, degraded mode; dashboard/Telegram should never be the only safety mechanism.
5. **Financial:** hard limits on AI provider calls/spend, hosting/data requests where measurable; disabling costly AI must not disable existing position monitoring.

## Failure matrix

| Failure | New entry behavior | Existing position behavior |
| --- | --- | --- |
| Stale or missing critical price/depth | Block | Mark state degraded, use predefined independent monitor and recovery procedure |
| Model timeout/invalid schema | Abstain | Deterministic risk continues |
| Rate limit/data vendor outage | Block dependent strategy | Retain/reconcile state and alert |
| Loss/drawdown limit breach | Block new exposure | Apply preapproved unwind/hold protocol; do not assume fill possible |
| Order response lost | **Do not blindly retry** | Reconcile against durable order IDs and venue state |
| DB outage / failed durable write | Block | Enter safe degraded state, reconcile after recovery |
| Budget reached | Stop billable AI entries | Deterministic monitoring remains active |
| Conflicting venue/pool identity | Block | Flag and request deterministic resolution |

## Future live-trading release checklist — all BLOCKED until explicit approval

- [ ] Independent profitability/risk review, including uncertainty and realistic fills.
- [ ] Venue account eligibility, local legal/regulatory and tax considerations reviewed for Bulgaria; platform access alone is not sufficient.
- [ ] Official supported execution interface verified; no unapproved browser-order automation.
- [ ] Authenticated live adapter reviewed, tested in an appropriate sandbox and separately enabled by explicit config/approval.
- [ ] Isolated, limited-funding account/wallet, key rotation/revocation and withdrawal controls.
- [ ] Approved numeric limits and emergency controls; operational runbooks and 24/7 response expectations.
- [ ] Reconciliation, idempotency and real-fill accounting tests pass; pre-production incident simulation.
- [ ] Human authorizes venue, capital amount, strategy, risk profile and deployment revision.

This list is a gate, not a preauthorization.


## M8A review corrections — 2026-10-23

The order-notional cap controls new exposure, not a reducing sale or full-liquidation
valuation. A qualified exit still needs held quantity, matching identity, current
original evidence, executable remaining depth and the existing fee/cost checks.
A submitted pause/stop is ordered by command time, independently of any modeled
future execution latency. These corrections do not change approved risk fractions.

Collection failure and clock-gap entry suspensions clear automatically only after
three consecutive fresh qualified ticks, within a per-session breaker of five clears
and three per rolling hour (DEC-039); beyond it, explicit manual recovery is required.
A manual pause, financial risk latches and capacity are never cleared automatically.
Bounded process restart does not clear those reasons or financial risk latches. A readonly progress probe distinguishes stalled/terminal workers from
recent durable activity; it is not market-data readiness or guaranteed alert delivery.
Autonomous settlement/unwind and cross-session capital carry-forward remain pending
separate decisions; no completed realized-return claim follows from marked positions.
