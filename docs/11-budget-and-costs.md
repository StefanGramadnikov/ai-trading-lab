# 11 — Budgets, model/provider costs and ROI

**Status:** €100 experimental expense ceiling preference and initial ≤€100 possible trading capital APPROVED; line-item allocations ESTIMATED. **Date:** 2026-10-17.

## Two distinct budgets

1. **Experiment operating expenses:** aim at **no more than €100 per initial experiment**; minimize spend. AI API, market data, hosting, telemetry and other operating cash expenses belong here.
2. **Potential future live trading capital:** ≤€100 initially, flexible only with a **new explicit user decision**. Entirely separate from subscriptions/expenses. **€100 virtual balances are not deposits.**

The project involves multiple experimental cells; the **total aggregate expense ceiling and per-cell allocation** still need user confirmation before paid APIs run. Until then, prefer zero-cost fixtures and read-only integrations, and seek approval before committing to a paid service.

## Preliminary planning example — not a quotation

| Category | Proposed envelope | Notes |
| --- | ---: | --- |
| Local development | €0 incremental | Personal Mac and existing tools; electricity not included |
| VPS for initial cloud period | ~€5–15 | Vendor/storage/region need quote |
| OpenAI API pilot | ~€10–20 | Requires separate personal API billing |
| Alternative provider API pilots | ~€10–20 | Only if warranted; no company Claude use |
| Data sources | €0 initially | Free tiers/rates not guaranteed |
| Reserve | Remainder under approved ceiling | Charge overruns and unexpected storage |

Not all allocations should be activated simultaneously. ChatGPT Plus pays for planning/Codex eligibility **but does not inherently provide developer API credits**. Provider billing and subscriptions change over time; verify official pricing immediately before purchase/use.

## Runtime cost controls

- Global and per-provider **hard application-side** daily/monthly experiment budgets; config requires approval and is versioned.
- Per-run cap on model calls, prompt input tokens, output tokens, tool invocations, retries, analysis interval and concurrent agent roles.
- Separate estimated and actual charged costs; if vendor billing data is unavailable, flag estimate and disable calls at conservative buffer thresholds.
- Record currency and FX rates with timestamp; avoid mixing USD API bills with EUR capital without conversion.
- Budget exhaustion halts **new billable inference**, not deterministic existing-position monitoring.
- Manual activation only for a new paid provider; never have Codex auto-purchase anything.

## Definitions

To avoid double-counting:

```text
Gross trading result before friction
  - execution fees, network charges, spread, impact, modeled slippage [only if not already in fill prices]
  = trading net result
  - experiment direct AI costs
  - experiment direct data/hosting charges
  - allocated shared recurring operating costs
  = experiment economic net result
```

Also report **consolidated platform economic net result**, subtracting shared operating bills **once** across the whole system. Paper returns are simulated, while paid API/hosting costs are real cash expenses. Keep that distinction visible.

### ROI and scaling

- Trading ROI: period trading net P&L / defined invested virtual capital.
- Economic ROI: net result / clearly declared total economic capital base. A paper trading strategy using €100 with €20 actual monthly operating bills has different economics than the same percentage return on €1,000.
- Report annualization only with clear assumptions; 30 days of high-volatility memecoin gains are not a reliable annual forecast.
- Scale scenarios require liquidity/market-impact modeling, position concentration and venue volume limits; do **not** extrapolate linearly from a €100 account.

## Open purchasing decisions

- Provider/model selection and price per million input/output tokens.
- Whether to use xAI-managed multi-agent products or our own orchestration (cost and capability comparison).
- VPS provider and service class, backup costs, raw data retention and monitoring.
- FX conversion, VAT/tax treatment and attribution of shared costs.

Before every purchase, record vendor, plan/price, billing unit, renewal, rate limits, data use/privacy conditions, user authorization and experiment budget impact.
