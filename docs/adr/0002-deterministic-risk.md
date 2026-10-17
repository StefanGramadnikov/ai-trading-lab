# ADR-0002 — Deterministic risk controls and paper-only execution

- **Status:** APPROVED
- **Date:** 2026-10-17

## Context

Third-party trading-agent prompts can hallucinate, ignore limits and operate on stale price information. The user desires autonomous operation but initially approves paper trading only.

## Decision

AI components output **proposals**, not direct orders. A deterministic risk and execution layer applies hard constraints, validates market freshness and account state, and maintains an auditable order/ledger lifecycle. M0–M8 remain `PAPER_ONLY`; no live credentials or funded wallets. Any live pilot is a separately gated, explicit user decision.

## Consequences

- Better testability and accounting, narrower security surface and predictable failure handling.
- Some useful AI recommendations will be rejected; this is intentional.
- The source guide's AI-directed SIZE/FILLS/RISK roles are redesigned into code services.

## Rejected alternative

Give each AI agent a browser or wallet and allow self-adjusted limits at runtime.
