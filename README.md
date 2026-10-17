# AI Trading Lab

> **Status:** Milestone 0 — documentation approved as the initial project baseline; implementation not started.  
> **Document version:** 1.0-draft, 2026-10-17.  
> **Operating mode:** `PAPER_ONLY` until a separately approved live-trading release.

AI Trading Lab is a private, experimental platform for collecting market data, researching trading strategies, and comparing automated decision-making architectures. It initially targets **Solana memecoins** and **prediction markets** (Polymarket and potentially Kalshi). It is **not** a proven profitable trading strategy.

## Objectives

1. Implement **hybrid AI**, **multi-agent AI**, and **rules-only** approaches using shared market data and comparable simulation assumptions.
2. Test multiple strategy families, including an explicitly labeled **GRONK-inspired** baseline, without accepting unverifiable performance claims.
3. Compare **event-driven** and **periodic/continuous** AI analysis on profitability **after** fees, slippage, AI usage, and infrastructure costs.
4. Start on a Mac, run a **2–4-day local smoke test**, then run cloud paper-trading evaluation for **up to ~30 days** and hold an explicit **GO / EXTEND / STOP** review. Thirty days is a decision checkpoint, **not** proof of an edge.
5. Make the system capable of fully autonomous operation **only within deterministic, tested safeguards**. Real trading remains out of scope for the initial implementation.

## Read this first

| File | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Mandatory coding-agent instructions and safety rules |
| [Requirements](docs/02-requirements.md) | Approved commitments and explicitly unapproved proposals |
| [Architecture](docs/03-system-architecture.md) | Component contracts, data flow and operational boundaries |
| [Experiments](docs/07-experiment-design.md) | Fair comparisons, leakage avoidance and metrics |
| [Testing](docs/08-testing-standards.md) | Every-cycle automated quality gate |
| [Roadmap](docs/10-implementation-roadmap.md) | Milestones, sequencing and acceptance criteria |
| [Decision register](docs/12-decisions.md) | Approved decisions, assumptions and open questions |
| [Progress](docs/13-progress.md) | Current state, blockers and next action |
| [Codex kickoff](docs/14-codex-kickoff.md) | Copy-paste instructions for M1 after M0 review |
| [GRONK source notes](docs/15-gronk-source-review.md) | What the supplied guide actually claims and what remains unverified |

## Core safety rules

- **No live orders, no wallet private keys, no trading account credentials in M0 or M1.** Paper-trading is the only permitted execution mode until the user explicitly authorizes a separate live-trading milestone.
- AI output is **untrusted proposal data**, not an order. Deterministic code owns position sizing, risk checks, reconciliation, accounting and order routing.
- Missing market data, ambiguous market identity or expired prices **block new entries** dependent on that data. Do not hallucinate replacements.
- No auto-deployment of AI-generated strategies or risk-limit modifications.
- Financial performance claims require observed evidence and an explicit accounting definition. **No profit is guaranteed.**

## Repo status and first Git steps

The user reports having **created the repository**. Do **not** run `git init` in an existing initialized clone, and do not overwrite its files. Check first:

```bash
cd /path/to/ai-trading-lab
git rev-parse --is-inside-work-tree
```

If this returns `true`, Git is initialized. If it fails and this is the intended new project directory, run `git init`, as the user specifically requested to be told when Git initialization is needed. Then copy this package into the repo, inspect `git status`, and commit the M0 documentation **after review**.

The repo is intended to be **private**. Keep `.env`, API keys, session tokens, mnemonic phrases, wallet keys, and personal account exports out of version control.

## Scope boundary

M0 delivers **documentation only**. M1 will initialize tooling, CI, a health endpoint and a testable application skeleton. The presence of this documentation does **not** mean the infrastructure, APIs, cost tracking, backtests or trading bot have been implemented or verified.

## Document conventions

- **APPROVED:** explicitly agreed by the user in the planning conversation.
- **PROPOSED:** a technical recommendation needing validation; may change during implementation review.
- **OPEN:** a decision, integration or fact that requires investigation or approval.
- **BLOCKED:** must not proceed until a named gate is satisfied.

**Authoritative project history:** repository documentation and reviewed Git commits. Chat conversations are helpful discussion context but are not a substitute for a committed specification.
