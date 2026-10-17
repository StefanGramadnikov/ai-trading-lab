# ADR-0001 — Modular monolith with shared experiment infrastructure

- **Status:** APPROVED at architecture level
- **Date:** 2026-10-17

## Context

The project compares multiple strategies, markets, AI providers, agent approaches and risk modes with limited budget and maintenance time. Separate duplicated applications would produce inconsistent execution assumptions and excessive operating complexity.

## Decision

Use one typed Python codebase (modular monolith) with market adapter, strategy, AI provider/orchestrator, risk, simulation, persistence and reporting boundaries. Share market observations while isolating experiment configs, paper portfolios and audit trails. Use PostgreSQL, Docker Compose, lightweight FastAPI backend and simple dashboard as the **proposed M1 stack**.

## Consequences

- Lower hosting/maintenance cost, uniform accounting and fairer comparisons.
- Changes to shared services can affect all experiments; integration tests and immutable config/versioning are essential.
- Scale into separate workers or add queue/caching only upon measured bottleneck.

## Alternatives not selected initially

Separate app per bot, message-bus microservices, all-in-Grok hosted agent workflow.
