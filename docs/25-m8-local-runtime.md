# 25 — M8A local runtime foundation

Approved 2026-10-20: user “OK, continue with the next phase” then “OK continue”
after the M8A/M8B/M8C plan. M7 merged `7d4562fc2040a3e7e91f805f6eb982ba6043c8e5`.
Branch `feature/m8-local-runtime`. Low account allowance reported to user; durable
contracts/evidence must permit handoff. User subsequently invoked `/ship` for M8A;
commit/push/PR and bounded CI verification are now authorized, never merge or activation.

## Contract and work breakdown

- Add one optional worker from the same image, sharing PostgreSQL; no broker.
  Explicit bounded polling, single-owner protection, immutable configuration/code,
  append-only cycle checkpoints, restart without duplicate decisions or funding.
- Persistent per-session virtual portfolio; retain positions, fees, source-depth
  consumption, day opening realized net, peak NAV and entry-suspension latch.
  M3 accounting/simulation and M4 rules remain authoritative. No operational risk
  numbers chosen by the agent: complete explicit risk/simulation profile required.
- Input is either synthetic fixture frames or original OBSERVED Polymarket captures
  qualified with existing ReplayEntry native identity/description/depth/time checks.
  This is observation-driven PAPER simulation, never a venue order. Missing forecasts,
  other unqualified sources and stale data abstain; never relabel fixture as observed.
  Other source adapters can collect evidence but cannot invent executable depth.
- Freeze code/source identity. Cycle IDs/native quote event IDs deduplicate retries;
  event chain and full deterministic replay verify checkpoint state. Database or
  code/clock conflicts block activity. No blind catch-up or new portfolio on restart.
- Explicit stop/pause/resume controls separate from read-only dashboard. Stop/pause
  block entries; deterministic exits require qualified marks. Unavailable NAV blocks
  entries; no guaranteed unwind. Clock gap degrades; no assumed fills during sleep.
- Bounded requests, cycle count, total runtime, journal bytes, trade history and logs.
  Stop admission at capacity; do not prune evidence or silently extend caps. Heartbeat
  records worker progress and diagnostic failure; API health stays separate.
- Backups/isolated restore verification and startup/migration/rollback runbook.
  No restore over an existing database, destructive data retention or public ports.
- Unit/real PostgreSQL/contract/fault/browser tests, full make check, production build,
  migration parity, controlled short fixture smoke and fresh independent review.
  Update README, ADR, decision register and progress with observed results only.

## Approval gates

M8A is implementation only. Any real market acquisition or operational activation
gets an explicit finite invocation/configuration; no unattended launch in this task.
M8B 2–4-day local smoke requires shipped reviewed code, approved parameters and
manual Mac sleep/network setup. M8C vendor/cost/cloud deployment is separate.
AI is disabled in operational runtime until a new provider/model envelope approval;
no fixture model is represented as real inference. Telegram real sends remain gated.


## Concrete persistence and source contracts

Runtime state/receipts reside in runtime_sessions/runtime_events (migration0010),
not mutable M4/M6 results or a new allocation of capital per poll. Full recovery
replays final state once and checks historical checkpoint hash chains; no DB-owner
tamper-resistance claim. Worker operations retain the lock-owning physical connection.
Bounded read-only source batches are durably reserved before network and count
against session limits across restarts. See ADR-0011. The collector supplies no
forecast, so a probability strategy without supplied qualifying evidence abstains.

Canonical journal evidence is capped at64MiB; this is not PostgreSQL physical disk
usage. Disk sizing/retention decisions are M8B acceptance prerequisites, not invented
current capabilities. Superseded by DEC-040 (2026-10-25): M8B is a no-trade infrastructure
soak, so a forecast/feature source is no longer an M8B prerequisite; it, an exit/settlement
path with captured resolution evidence, and a session-chaining decision gate any run that
can hold positions.

**M8B places no orders.** The collector supplies no forecast or feature, so the rules
strategies abstain and no position can open; reports remain mark-only
(`economic_basis: mark_only_no_realization`, `final_pnl_claim: false`). A multi-day
"RUNNING, no losses" result is infrastructure evidence only, never strategy evidence.


## Local acceptance — 2026-10-20

Full make check PASS:1022tests/95.36% statement/branch coverage, Ruff/mypy142files.
Fresh independent review PASS after two bounded repair rounds. Controlled fresh
production image/migration/schema parity, packaged CLI/HTTP/read-only runtime
panel, persistent portfolio/replay and actual private PostgreSQL backup/isolated
restore/full-checkpoint replay PASS. No operational worker, real market acquisition,
paid inference or Telegram delivery was activated. Exact commands, fingerprints,
quota interruption and repaired evidence are recorded in document13.
The user invoked `/ship` after local acceptance. Hosted CI must pass on the final
PR head; merge and M8B/M8C activation remain separately gated.

## Independent review repair plan — 2026-10-23

User authorized verifying CODEX_FIXES.md against binding requirements, regression
first fixes and complete isolated verification. Work remains on the existing
branch without staging, commits, pushes, branch changes or external PR changes.
Preserve all existing demo containers/volumes/data. Use a new disposable PostgreSQL
server/database ai_trading_lab_test and remove only that server after verification.
Python3.12.13/locked dependencies and Chromium restoration downloads were approved.

Sequence: F01 reducing exits/marks; F02 provenance/leased polling; F03 incremental
journal/capacity plus measured real-reducer benchmark; F05 cross-envelope spending
and trusted pricing; F04 bounded supervision/staleness only; then remaining verified
medium findings. Each behavioral change needs an observed failing regression before
implementation, focused repeat, then the complete format/lint/mypy/coverage suite,
production build/Compose validation/migration roundtrip and fresh independent review.

F05 narrower scope explicitly approved: retain ADR0009's prohibition on an
administrative reconciliation override; add global guards without that command.
F04 automatic clearing of collection_failure/clock_gap was approved on 2026-10-25
(DEC-039): three consecutive fresh qualified ticks, breaker of five per session and
three per rolling hour, never a pause/risk latch/capacity/stop. F11 chaining
and F12 new exit/settlement behavior are deferred; implement historical read access,
documented single-session limits and mark-only/no-realization labeling only.
F10/F17 use synthetic negative/contract tests and describe required real captures;
no public requests are authorized. The user explicitly approved the F03 maximum
configurable canonical journal limit of512MiB. Per-session limits remain explicit;
that approval does not establish measured storage/latency or physical disk quotas.
Increasing a cap alone cannot resolve checkpoint copying or whole-tape replay.
