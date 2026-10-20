# 23 — M6 experiments and economic evaluation

**Approved:** user approved the presented M6 plan on 2026-10-20.
Baseline merged M5 `519ee2b`; branch `feature/m6-experiments`. Local implementation
only; shipping, recurring paid calls, M7, deployment and live trading remain gated.

## Contracts and increments

1. Frozen experiment/cell/profile configuration; bind complete JSON and runtime
   fingerprint durably before evaluation. Explicit comparison dimension permits
   only its declared changes, with identical chronological dataset/window/capital
   and simulation assumptions otherwise. Development provider smoke is excluded.
2. Durable lifecycle CREATED → RUNNING → EVALUATING → APPROVED/REJECTED, with
   PAUSED/manual resume and append-only idempotent command events. Approval is an
   evaluation decision, never deployment authority. Bounded supplied-tape cells
   commit atomically; manual pause takes effect between cells, not as a real-time
   emergency stop. Repeated completed cell requests replay, never re-execute.
3. Cell runner reuses deterministic M3 sizing/risk and M5 evidence/orchestration/
   budget boundaries. M6 AI activation is fixture-only; no extra paid call is
   authorized. Three named paper profiles require explicit immutable numeric
   limits; examples are synthetic/nonoperational. Daily loss uses realized gross
   less paid fees against opening cash; drawdown uses qualified liquidation NAV.
   Breaches latch entry/inference suspension for the rest of the tape; deterministic
   exits and marking continue. Unavailable marks suspend entries conservatively.
   The experiment pauses after that cell, before another cell may start.
4. Cost records distinguish estimates/invoices, direct/shared allocations and
   currencies. Replace an estimate with its linked invoice, never sum both.
   Fixed explicit weights allocate shared bills once; consolidated totals count
   each effective expense once. Unknown amount/FX/billing remains unavailable.
   All-expenses economic results require an explicit completeness declaration;
   generic collateral is not USD/EUR. No live FX retrieval or purchasing.
5. Reproducible reports include paired deltas, NAV curve, drawdown/exposure,
   execution/AI counts, costs and limitations. Fee/slippage already embedded in
   fills are never subtracted twice. Unresolved/unmarked positions and unsupported
   sample statistics remain unavailable, not fabricated zero/profit/calibration.
6. Shared run-ownership registry prevents collisions across experiments and M4/M5
   runners, including legacy records. M4 claims commit with its final atomic run;
   AI/experiment ownership binds before any provider request. Cohort run IDs cannot
   be reused by cloning only an experiment ID. Public reports retain complete NAV
   curves including terminal marks at the declared endpoint, not the last tick.
7. New `experiments/` modules, `atl-experiment` CLI, new migrations 0007/0008, synthetic
   example and unit/real-PostgreSQL/CLI tests. No dependency/service/worker/UI added.

## Acceptance and failure cases

Reject mismatched tapes/windows/currency/capital/undeclared dimensions and mutable
run identities. Verify lifecycle invalid transitions, duplicate commands, timestamps,
concurrent advances and atomic rollback/restart. Test loss/drawdown equality,
missing marks, continued deterministic exits, no AI after suspension, all three
profile names and independent ledgers. Cost tests cover estimate replacement,
allocation conservation, duplicate IDs, missing amounts/FX and consolidation.
Metrics tests cover fee arithmetic, unresolved holdings, insufficient/irregular
samples, UTC and Decimal context independence. Replay must verify report/projections
and recorded AI/cost history. Run full `make check` with isolated PostgreSQL,
production migration/drift/fixture-repeat/health checks and fresh independent review.
Update docs/13-progress.md with observed results; no green claims before execution.


## Acceptance evidence — 2026-10-20

After Docker recovery, complete quality gate and PostgreSQL regressions passed:
846 tests, 96.62% coverage; Ruff and strict mypy (106 files) passed.
Production image build/startup, migration head 0008/schema parity, four-cell CLI
lifecycle/pause/repeat and HTTP health passed. Reports are identical on replay,
installed-source fingerprints match the host, and per-cell fixture call counts are
0/1/3/6. Synthetic shared cost 0.04 is counted once. Independent development and shipping re-reviews
passed after the recorded bounded repairs. See document 13 for exact final counts,
commands and incident history. No additional paid API call or shipping occurred.


Shipping hardening uses the simulated execution/valuation UTC date for daily-loss
rollover. Decision timestamps plus latency can cross midnight; losses/fees belong
to their execution day, not the decision day. Both directions have regressions.
