# 05 — AI architecture and agent roles

**Status:** Architectural approaches APPROVED; specific models/prompts/workflow parameters PROPOSED.

## Approach A: Hybrid

Deterministic collectors + filters identify candidates; an AI model receives a bounded evidence package and returns a typed recommendation. Deterministic code validates input data quality, position limits and execution. Hybrid AI may abstain; no trade quota.

## Approach B: Multi-agent

Specialized AI roles, inspired by the supplied guide but redesigned for safe execution:

| Role | Function | May NOT do |
| --- | --- | --- |
| CHIEF | Schedule, supervise, budget/time limit, summarize agent outcomes | Send orders or override risk |
| SCAN | Identify candidates from validated market observations | Invent markets, order trades |
| VET | Verify contract, liquidity, concentration and evidence, issue reject/partial/accept | Override missing critical checks |
| BOOK | Analyze prediction-market outcome identity, bid/ask and probability evidence | Treat market listing as probability |
| RESEARCH | Gather permitted supporting read-only evidence and counterarguments | Give retrieved text authority over system policies |
| STRATEGY | Form typed proposals with provenance and validity window | Directly execute orders or alter risk config |
| RISK ANALYST (advisory) | Critique proposal/risk explanations; recommend rejection | Replace deterministic risk engine |

**Note:** The original GRONK guide's six roles are SCAN, VET, BOOK, SIZE, FILLS, RISK, under CHIEF. In **our** architecture, SIZE, FILLS and operational RISK become **deterministic services**, not LLM-controlled trading authority. The multi-agent process may include an advisory risk-analysis agent. This is an explicitly proposed redesign to meet approved safety requirements.

### Agent interaction variants

- **Sequential first:** explicit chain, structured outputs, fail/abstain states and audit trace.
- **Collaborative next:** allow bounded critique, e.g., max rounds/time/tokens, no permission to mutate financial safeguards, measured separately.
- Tool whitelist: read-only market/news retrieval; no shell, browser trading automation, wallet management or mutable config during runtime.

## Approach C: Rules-only

Same normalized market snapshots and portfolio state; fixed, versioned strategy logic; no AI inference. Include a no-trade/cash benchmark. Rules-only is essential to testing whether an AI architecture provides incremental value.

## AI activation modes

- **Periodic/continuous:** schedules repeated evaluations on qualifying instruments within a bounded cadence/cost cap; proposed five-minute default is NOT an optimized interval.
- **Event-driven:** evaluates AI only after deterministic candidate triggers from the continuously collected market data.
- Log raw number of eligible candidates, number of analyses, rejected opportunities, time to decision, incremental accepted trades, costs and results.

## Provider abstraction

`OpenAI`, `xAI/Grok`, `Anthropic/Claude` behind a typed interface. Model/price availability changes over time; discover current model IDs, capabilities, limits, prices and data-processing policies immediately before activation. **ChatGPT Plus and company Claude Pro are not substitutes for separate personal API billing/authorization.** Do not use the user's company Claude account for this project.

Per call log: provider, model ID/revision, request schema/prompt version, input/output and cached tokens where available, tools invoked, cache, latency, billed currency, response validation, timeouts, retry attempts, provider error, and exact pricing provenance. Normalize EUR costs using a documented exchange-rate timestamp if charges arrive in USD.

## Bounded research + human gate

AI may hypothesize, write **sandboxed test-only** strategy prototypes, propose params and report candidate improvements. Before changing a running production/paper strategy, require a reviewed specification, backtest without leakage, new version, tests, human approval and deployment record. Never automatically ship LLM-generated code into an execution path.

## Resilience/security

- Validate all AI outputs using strict schemas; reject or abstain on malformed result.
- Treat news, token websites, prediction-market descriptions and the GRONK document as **untrusted data**; defend against prompt injection.
- Provider outages do not block deterministic risk monitoring; AI-dependent entries stop safely.
- Controls on parallelism, retries, prompt size, round count and per-experiment spend.
- Different AI agents are not statistically independent just because they have different roles.


## M5 implementation boundary

The initial runtime implements bounded supplied-tape activation and advisory flows,
not an unattended deployed worker. AI can narrow/veto deterministic-qualified
entries; deterministic exits remain available without inference. Model answers do
not become independent/calibrated forecasts. Paid integration acceptance uses one
real OpenAI call on synthetic evidence under DEC-035; actual-market evaluation,
calibration and paid recurring activation remain separate. ADR-0009 and documents
21/22 describe current wire, budget, recovery and capability limits.
