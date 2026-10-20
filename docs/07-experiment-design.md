# 07 — Experiment design, comparison, and success criteria

**Status:** Comparative research APPROVED; statistical thresholds and sampling sizes OPEN. **Date:** 2026-10-17.

## Questions the experiments must answer

1. Does any tested strategy earn a positive *net* simulated return after realistic friction?
2. Does **Hybrid AI** improve outcomes versus the **same rules-only baseline**?
3. Does **multi-agent** improve on Hybrid once cost and latency are included?
4. Does **periodic/continuous AI** deliver enough incremental benefit over **event-driven** AI to justify extra calls?
5. Do model providers differ on the *same* archived decision inputs?
6. Does higher risk allocation improve risk-adjusted return or only increase volatility and loss?

## Design constraints

- **One variable at a time where possible:** align market, strategy, decision windows, risk profile, initial virtual balance, simulation model and model/provider when testing the isolated dimension.
- Maintain a common raw feed; freeze immutable snapshot IDs or replay tapes for paired comparisons. Store ingestion time and information availability time, **not just market event time**.
- All configurations are versioned and timestamped. No mid-run tuning of an existing experiment; create a new run/version instead.
- Start parallel head-to-head comparisons only when *both* relevant implementations are ready; earlier Hybrid development trades are exploratory and excluded from the matched comparison.
- The declared test set is not repeatedly used for parameter search. Train/validation/untouched evaluation splits should respect chronology; report the number of comparisons and guard against selecting the luckiest configuration from dozens.
- Market coverage and exposure constraints are recorded; a simple €100 paper portfolio per cell does not imply simultaneous €100-funded live accounts. Later shared-capital simulations must enforce the **total** planned live capital ceiling.

## Experiment matrix, staged for cost

| Stage | Purpose | Examples | Exit gate |
| --- | --- | --- | --- |
| E0: data qualification | Validate sources, identity, timeliness and cost | Token launch coverage, prediction order books, historical gaps | Recorded data meets defined quality bar |
| E1: deterministic research | Establish baselines and simulator validity | GRONK-inspired rules-only, simple probability mispricing | Accounting and replay tests pass; enough valid candidates |
| E2: architecture | Hybrid vs multi-agent vs rules-only, matched periods | Same candidate sets, risk and simulation | Difference in net results/latency/cost reported |
| E3: activation | Periodic vs event-driven at same provider/strategy | Five-minute periodic as proposed pilot vs trigger-based | Marginal value per call reported |
| E4: providers/agent collaboration | GPT vs Grok vs Claude and sequential vs collaborative | Identical archived inputs | Results justify the API cost |
| E5: deployment evaluation | Local 2–4-day smoke, cloud paper period up to ~30 days | Real-time simulated operation | Human GO/EXTEND/STOP review |

**No requirement to activate all combinations simultaneously.** A combinatorial full-factorial run would exceed the time/cost budget and yield noisy comparisons.

## Virtual capital and accounting definitions

- An experiment may use a **€100 simulated opening balance** as a comparable reference, not a deposit. Use consistent currency conversion and distinguish USD-denominated fees and crypto assets.
- Record opening cash, reserved cash, trades, gross realized trading gains/losses **before fees**, unrealized marked exposure, trading fees, spread/impact/slippage, network costs, financing/settlement costs if any, AI bills, allocated data/hosting expenses and closing NAV.
- **Trading net P&L:** gross trading P&L minus all trading friction (avoid subtracting any component already included in fill-price-based P&L).
- **Operating net profit:** trading net P&L minus attributable AI/data/hosting/other ongoing cash expenses.
- **Net ROI:** net profit divided by *defined capital base* (initial capital and capital added; no added capital in initial tests), with period length and sample size always stated.
- Mark *unresolved* prediction positions separately; count realized settlement P&L only when settled; show NAV under fair-value assumptions with sensitivity.
- Cost attribution: direct AI costs to experiment; allocate shared infrastructure by recorded usage or transparent fixed share. Also report consolidated actual cash spend to prevent double-counting shared expenses across experiments.
- Development cost and ChatGPT Plus used for development are **separate from recurring running costs**, but optional full-economics views can include an amortized development allowance and subscription share with explicit methodology.

## Metrics

| Area | Required metrics |
| --- | --- |
| Returns | Gross P&L, trading net P&L, operating net P&L, NAV, return by interval |
| Risk | Max drawdown, volatility where sample permits, max exposure, worst trade, tail outcomes, underwater time |
| Trading | Fill ratio, slippage/impact distributions, round-trip fees, trade count, holding period, rejected/abstained proposals |
| Models | Token/call/tool costs, latency, timeouts, errors, cost per screened candidate/filled trade |
| Prediction | Resolved event outcomes, calibration, Brier/log-loss where appropriate, contract liquidity/settlement state |
| Operational | Data freshness/coverage, uptime, missing samples, restart recovery, budget-limit incidents |
| Reproducibility | Git SHA, config hash, run ID, strategy/prompt/model IDs, price-model version, exact data window |

## Evaluation timeline and decisions

- **2–4 local days:** engineering smoke test. No profitability conclusion; check restart recovery, data quality, dashboards, costs and simulated execution.
- **Up to 30 cloud paper days:** official staged evaluation; review head-to-head only over overlapping observations. The longer a prediction contract takes to resolve, the less a 30-day paper test can prove about it.
- **Day-30 decision:** `GO` means **begin separately approved live-pilot preparation**, *not* enable trading. `EXTEND` for promising/inconclusive evidence. `STOP/REDESIGN` for loss, poor economics or unreliable data.
- No fabricated promise of significant statistical confidence within 30 days; results may be indeterminate even with many highly correlated trades.

## Stop/pause conditions

Hard stop if risk/budget/data-security boundaries breached. Pause for integrity failures or inability to model execution. Poor ordinary returns initiate review after prespecified minimum data rather than indiscriminate early termination; report both planned and actual stopping rules to avoid outcome bias.

## Interpretation

Positive 30-day results, especially from volatile memecoins, are **not** proof of future profitability. Backtests and paper results cannot guarantee live fills or returns. Report uncertainty, alternative assumptions and the limits of the available sample.


## M6 executable comparison contract

Declare architecture/activation/provider/risk as the single varying dimension.
Shared tape, window, strategy, capital and baseline simulation must match exactly.
M6 fixture AI is engineering evidence, not a paid-provider or calibrated-forecast
experiment. Each cell has an independent ledger, immutable configuration and source
identity. Minimum frames/fills are configured, not claims of statistical power;
zero fills may be valid when the declared minimum is zero. Missing cost/valuation
evidence blocks approval. APPROVED is an explicit evaluation event, not deployment.
