# ADR-0003 — Reproducible staged comparisons rather than full experiment explosion

- **Status:** APPROVED principle
- **Date:** 2026-10-17

## Context

Markets (2+) × strategy families × 3 architectures × two activation modes × three risk levels × three AI providers creates an impractically large and expensive search space. The project must estimate **incremental net** returns per AI architecture, not simply report winner's luck.

## Decision

Use staged experiments, immutable versioned configs, paired market snapshots/time windows, a rules-only baseline and cash benchmark, held-out evaluation and explicit expense tracking. Run AI provider/activation/agent comparisons only on promising strategy cohorts, while preserving source and outcome records.

## Consequences

- Affordable sequential evidence gathering and clearer causal interpretation.
- Not every configuration will be tested in the initial 30 days; gaps must be reported transparently.
- A 30-day go/no-go discussion can result in EXTEND because markets and outcomes may be insufficient.
