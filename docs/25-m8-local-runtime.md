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
usage. Disk sizing/retention decisions and a valid operational source/forecast
configuration are M8B acceptance prerequisites, not invented current capabilities.


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
