# 21 — M5 AI providers and bounded paper research

User approved the M5 implementation plan and a final real-provider smoke test.
Baseline: merged M4 `fec599d`; branch `feature/m5-ai-runtime`. No shipping, M6,
live trading, deployment or automatic strategy deployment is authorized.

## Increments and interfaces

1. Frozen evidence, request, response/usage and orchestration contracts. Runtime
   models receive bounded available evidence; all descriptive text is untrusted.
   They can propose/abstain/veto only, never size orders or change controls. AI
   assessments are not relabeled independent/calibrated Forecast records.
2. Real HTTP adapters for OpenAI Responses, xAI chat completions and Anthropic
   Messages, plus a deterministic fixture adapter. Fixed provider URLs, no external
   tools, no redirects, bounded request/response/time, strict local validation,
   refusal/error handling and sanitized secrets. Contract tests inject transport.
3. Durable versioned spending envelopes and call reservations in PostgreSQL. Reserve
   conservative worst-case model cost before sending; never retry an uncertain
   request automatically. Repeated completed calls replay recorded results; pending
   or unknown outcomes retain their reservation and block new paid inference until
   explicit reconciliation. Usage-derived costs are estimates, not invoices; keep
   currency/pricing provenance and global/provider/run/day/month caps explicit.
4. Hybrid, sequential and bounded collaborative role flows; recorded trace with
   maximum steps/rounds/tokens/time. Read-only evidence access is local and bounded;
   no shell, arbitrary retrieval, broker, wallet or mutable risk tools. Deterministic
   vetting gates before AI; advisory vetoes cannot override deterministic rejections.
5. Periodic/event-triggered evaluation over an explicit supplied chronological feed,
   durable immutable AI runs and CLI. Reuse M3 sizing/risk/accounting and M4 reports
   for rules/cash comparisons without rewriting old runs. Recorded AI responses are
   replayed, not billed again. Fresh models on historical inputs are development
   evidence, not contemporaneous/held-out historical forecasts.
6. Unit/contracts/real PostgreSQL/end-to-end fixture tests, full make check, container
   smoke, independent review, README/config/docs/progress updates. New migration
   0005 plus shipping recovery checkpoint 0006; no old migration edits, service, SDK or dependency is presumed necessary.

## Final acceptance and user activation

Fixture integration is necessary engineering evidence but does not establish real
provider integration. Final M5 acceptance includes one bounded real-provider smoke
through the same adapter, durable budget ledger and paper decision path. Provider,
exact model, current price/capability, total spending cap and local personal API key
must be established before a paid call. Do not request keys in chat or inspect/echo
secrets. No purchases/company billing. User selected OpenAI with a US$1 total cap and
confirmed local key setup. The reviewed smoke fixes gpt-5.6-terra, standard tier,
no reasoning/tools, one request and a conservative US$0.053248 reservation.
Only run after the full gate and independent review pass.

No evidence qualification is relaxed to manufacture a fill. A valid abstention or
risk rejection is a valid smoke outcome. No genuine market performance, model
calibration, exact invoiced cost, data authenticity or legal access is inferred.

## Ownership and verification

Root owns shared models/evidence/orchestration/feed/CLI and paper integration/docs.
Provider and durable-budget modules may use narrow agents with exact ownership.
Independent fresh-context review covers the complete diff and tests. Verify invalid
schemas/injections, native IDs, times, stale/partial evidence, price/quantity/risk
separation, role/round limits, trigger deduplication, cost/token limits, uncertain
outcomes, retries, concurrent reservations, restart/corruption/rollback and recorded
response replay. Run all existing M1–M4 cases and the full deterministic gate.


## Observed acceptance — 2026-10-20

Full gate PASS: 684 tests, 96.34% coverage, Ruff/mypy; production migration,
health and fixture repeat PASS; independent AI re-review PASS. One real OpenAI
request completed and validated, 797 input/155 output tokens, USD0.0038525
conservative estimated expense. Exact replay retained one recorded call without
another request. Synthetic evidence produced one simulated fill through M3 controls.
See document 13 for commands, recovery repairs, limitations and audit details.
Shipping/hosted CI and M6 remain separately gated.


Shipping recovery hardening: commit an immutable run-start checkpoint containing
full input and installed-source fingerprint before provider admission. An interrupted
run rejects altered sizing, simulation/risk controls or runtime; exact recovery uses
recorded responses. Migration 0006 preserves completed development identities and
refuses old incomplete calls without full configuration. See ADR-0009.
