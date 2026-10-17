# 01 — Project overview

**Status:** APPROVED goals, PROPOSED detailed design. **Last updated:** 2026-10-17.

## Mission

Build an automated, AI-assisted trading **research and paper-trading platform**, evaluate whether any supported strategy demonstrates a credible positive edge after realistic costs, and determine whether the system has commercial potential. The end-state is intended to be autonomous, but production live trading is **not approved**.

## Success means

- **Technical:** data is collected and validated reliably; experiments and simulated orders are reproducible; controls remain effective through failures/restarts; automated tests pass every development cycle.
- **Trading:** at least one strategy demonstrates positive out-of-sample results **net of trading fees, slippage, spread and other execution costs**, with drawdown and uncertainty reported.
- **Economic:** prospective ongoing profits also exceed incremental AI, data and hosting expenses. Results must not depend on unsupported fills or invented probability estimates.
- **Operational:** the user can maintain the system with low ongoing attention (target roughly ≤2 hours/day after setup, from the project's wider business objective) and fit first experiments into the agreed spending limit.

**Non-promise:** A successful implementation may correctly conclude that tested strategies are **not profitable**. Thirty days of paper trading may be inconclusive. This is a valid scientific outcome, though not commercial success.

## Initial scope — APPROVED

- Markets: Solana memecoins **and** prediction markets from the outset of architectural design. Implement both incrementally.
- Strategy families: GRONK-inspired, momentum, breakout, trend-following, mean-reversion, probability mispricing, and carefully verified cross-venue discrepancies; compare rather than choose by intuition.
- Architectures: hybrid AI, specialized multi-agent AI, and rules-only benchmark.
- AI providers: model-agnostic support for OpenAI, xAI/Grok, Anthropic/Claude where available, without requiring immediate subscriptions.
- Agent modes: sequential and collaborative; AI activation: periodic/continuous and event-driven; risk profiles: conservative, moderate, aggressive in paper trading.
- Autonomous research with controlled deployment; no agent can self-deploy into production or change live risk limits.
- Local Mac smoke test for **2–4 days**, then cloud paper-trading evaluation for **up to 30 days** and a human GO/EXTEND/STOP review.
- Dashboard + Telegram; local-to-cloud Docker deployment.
- Mandatory Git, private repo, full testing discipline and CI.

## Explicitly out of scope for M0/M1

- Live order execution, funded wallets or exchange API trading credentials.
- A claim that the GRONK return story is independently validated.
- Buying an xAI, Claude, market-data or other subscription before its need/cost is justified.
- Automatically generated strategy code being deployed without review.
- High-frequency market making, guaranteed arbitrage, or institutional-grade low-latency infrastructure.

## User access (reported, not programmatically verified)

- Personal ChatGPT Plus; Codex being installed on Mac.
- Company-managed Claude Pro, **not to be used** for a personal side project.
- No personal xAI/Grok API, OpenAI API or Anthropic API configured yet.
- Private Git repository created (actual local path/remote unknown).

## System-wide principles

1. **Evidence over enthusiasm:** distinguish simulation from observable executable prices and realized results.
2. **Comparability:** paired snapshots/time windows; identical cost/risk assumptions when isolating one independent variable.
3. **Safety:** fail-closed for new entries; deterministic trade execution and risk controls.
4. **Budget:** hard stop for billable AI research before costs exceed experimental limits.
5. **Reproducibility:** commit IDs, prompt/model versions, configs, data vintages and tested commands recorded.

See [requirements](02-requirements.md), [experiment design](07-experiment-design.md), and [decision register](12-decisions.md).
