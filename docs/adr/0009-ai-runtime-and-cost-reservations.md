# ADR-0009 — Bounded AI proposals and durable inference reservations

- **Status:** Implemented under approved M5 plan; local verification/review and real OpenAI smoke passed; shipping pending
- **Date:** 2026-10-19
- **Authorization:** DEC-034; OpenAI smoke US$1 total separately confirmed by user

## Decision

Keep the existing modular monolith, PostgreSQL and locked HTTP dependency. No agent
framework, scheduler service, provider SDK, broker or wallet is added. Runtime AI is
explicitly separate from coding agents and has no shell, execution, credential or
risk-configuration tool. Native instrument identity and cited local evidence are
validated; descriptions and prior answers stay untrusted data. Models emit typed
buy/sell proposals, abstentions or advisory vetoes, never executable quantities.

M5 adds Hybrid, sequential and bounded collaborative role flows with explicit
step/round/input/output/time limits. Deterministic rules qualify candidates first;
AI can narrow/reject those entries but cannot loosen their price or risk constraints.
Predefined rule exits remain usable without billable inference. Common M3 sizing,
admission, cost, depth and accounting are reused through the extracted
`execute_decision` helper; M4 behavior and tests remain required.

Periodic and event-driven triggers consume an explicit finite chronological tape.
There is no unattended worker or real-time deployment in this milestone. AI
assessments are not promoted to independent/calibrated prediction Forecast records.
Fresh API calls on old market inputs cannot establish contemporary historical
forecasts; M5 real activation is an explicitly synthetic integration smoke. Fixture
provider responses can exercise both FIXTURE and qualified archived tapes, always
with explicit inference labels and no empirical performance claim.

## Durability and spending

Migration `0005_ai_runtime` adds immutable envelope policies, per-call reservations,
results and AI run records. One versioned envelope is shared across configured
providers/runs; UTC day/month, global/provider/run amounts and call counts apply.
A PostgreSQL envelope-row lock spans admission and reservation persistence; HTTP is
outside that transaction. Reserve the maximum configured input/output cost using
explicit USD prices/provenance before sending. Never mix this expense ledger with
virtual portfolio cash. No currency conversion or cost-allocation claim is added.

Provider usage yields conservative estimated cost, not an invoice. Missing usage,
unknown outcomes, wrong model/tier, excessive usage or unsupported billed tools
retain a reservation and block new calls. Timeout/cancellation does not prove no
billing. No automatic retries, fallback provider or new budget envelope is used to
bypass uncertainty. There is no administrative reconciliation override in M5;
unknown outcomes require manual investigation and a separately reviewed resolution.
A pending call can be finished once when its actual outcome is recovered.

The CLI defaults to disabled real inference and reads personal API keys only from
local settings. Explicit activation and a current price snapshot are mandatory.
The approved smoke fixture fixes one OpenAI envelope at US$1 and at most one call;
reruns replay the recorded result. Never regenerate that envelope to retry a failed
unknown request. Model/tariff capabilities are constrained as documented in
[document 22](../22-ai-provider-contracts.md). Standard-tier, cache-write and reasoning
costs must be considered; no tools or implicit premium service tier is requested.

Migration `0006_ai_run_starts` adds a separately committed full-input/runtime
checkpoint before provider admission. It survives final-transaction loss and rejects
changed sizing, controls or code on recovery. Completed development runs backfill
their original identity; incomplete calls or malformed reports without a provable
start checkpoint are refused, never rebound to current configuration. Applied
migration 0005 is preserved unchanged.

An advisory transaction lock serializes a run while it evaluates; each provider call
commits independently first. The final AI report plus paper projections commit
atomically. If final persistence fails, retry reconstructs state from recorded calls
without new inference. A still-pending outcome blocks new inference. Reports and
complete paper projections are checked on replay; corruption or runtime/config
changes reject. Call checkpoints detect deleted/altered history. Database-owner
coherent rewrites remain outside the immutable security boundary.

## Evidence and limits

Record exact request/provider/model strings, prompt version, response hash/ID,
validated usage, monotonic elapsed milliseconds, receipt UTC, pricing/currency,
reservation and trace. A named vendor model/version is not proof of immutable
internal weights. Fixture clocks remain virtual; real price freshness uses UTC now.

Existing M4 reports retain open-position and unavailable-mark semantics. AI costs
are reported separately as USD estimates; all-expenses profit remains null because
operating costs/FX/allocation/calibration are unresolved. Synthetic tests do not
establish model quality, account eligibility or profitable strategies. The final
real-provider smoke is separate acceptance evidence and may legitimately abstain
or be refused by deterministic risk. Shipping, M6 and deployment remain gated.

## Amendment 2026-10-25 (V0 slice 3, DEC-044)

- **Direction-only origination.** In the live paper loop the model may propose buy, sell or hold, no longer only a veto.
  It still never sizes, prices, sets limits or touches risk: the deterministic code sizes every order from its own
  evidence and the mechanical exits (stop, take-profit, time) run first and never wait for the model. Its only inputs are
  numbers computed locally; its answer is validated against the supplied evidence ids and instrument key.
- **Unknown outcomes are settled, not frozen.** With `InferenceService(settle_unknown=True)` a sent call with no usable
  outcome is recorded as status `settled`, charged at its reserved maximum, so the spending cap holds without freezing
  the envelope. Without that flag (the experiment runner) the original freeze-until-resolved rule is unchanged.
- **Price window.** `reserve_cost` takes the maximum age of a reviewed price (`ATL_AI_PRICE_MAX_AGE_DAYS`, default 1 day).
  Stored-call integrity no longer depends on the clock at reload time.
- **Destination guard.** An `HTTPProvider` that builds its own HTTP client resolves the fixed provider host before any
  reservation and refuses to send the key unless every address is public; after connecting it also checks the peer when
  the transport exposes it. A refusal is local and costs nothing.
