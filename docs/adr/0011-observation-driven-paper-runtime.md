# ADR-0011 — Bounded observation-driven paper runtime

Approved direction: DEC-038 / user M8A plan approval, 2026-10-20.
Extends ADR-0007/0008 solely for observation-driven PAPER simulation.

## Decision

Keep one package/image/PostgreSQL; an optional Compose worker profile has no ports,
a failed-exit restart cap of three, read-only filesystem and bounded rotated container logs. Explicit
session configuration fixes capital, units, strategy, risk profile and simulation
assumptions. No operational numeric defaults are approved. AI is not a runtime tool.

Journal sessions bind configuration and installed Python source fingerprint before
work. Append-only events retain original commands/evidence once. Compact checkpoints carry
audit counts/digests rather than copying the entire prior command tape. A verified
incremental reducer advances normal work; mutation generation invalidates cached
state, and recovery replays the authoritative tape. Replay validates chain,
checkpoint consistency and refuses missing/corrupt/code-incompatible history. UUIDs
are scoped by runtime session; funding occurs once, never on a worker restart.
The journal is authoritative; runtime paper receipts/history are retained there,
separate from immutable M4–M6 research tables. No cross-period funding reset.

Only original observed Polymarket metadata/books can provide current executable
paper quotes. Existing raw ReplayEntry checks enforce native outcome/market/token,
resolution description, source/receipt times, two-sided depth and explicit generic
collateral. No USD peg or other source execution depth is inferred. A derived
transient REPLAY view feeds existing offline rules; durable raw/quote/order audit
remains OBSERVED. Forecasts/features are never manufactured by the collector, so
missing inputs cause abstention. This extends simulated execution, not venue orders.
M3History validates the raw entry inside each observed order application; a mutable
quote mode flag or runtime config marker alone cannot authorize it.

Native source/venue/instrument/event time identifies finite depth across capture
UUIDs. Duplicate deliveries cannot renew depth or refresh stale marks. Risk caps
are the tighter of explicit simulation and profile bounds. Realized gross less all
paid fees defines daily net; execution UTC date sets day opening. Qualified full
liquidation NAV drives drawdown. Missing marks block entries; risk breaches latch.
Pause/gap/failure block new entries while qualified deterministic exits remain
possible. Resume clears operational suspension, never a risk latch. Stop ends all
new cycles; it does not claim that positions were liquidated.

Amendment (DEC-039, 2026-10-25): `collection_failure` and `clock_gap` are the only
suspensions the reducer may clear itself. After a failure or a detected gap it counts
consecutive fresh qualified ticks (non-duplicate native event, VALID quality, advancing
event time, spaced within `max_gap_seconds`); the third clears the suspension for the
next tick's entries. A new failure or gap resets the count, and the gap-detecting tick
itself is never counted. A pause, a risk latch, capacity and stop are never cleared
automatically. A circuit breaker stops automatic clearing after 5 clears in a session
or 3 in a rolling hour; thereafter only an explicit resume clears. State records the
count, the breaker and the progress, and a clear is derived solely from journal events.

A PostgreSQL session advisory lock identifies one worker; its journal operations
use the same physical connection. A lost connection is never transparently replaced
under that lease. Explicit controls use ordinary serialized journal transactions.
Before HTTP, durable poll intent consumes a finite session attempt; interrupted
polls are recorded uncertain, never replayed as zero requests. Each batch has two
read-only requests, no retries; fixed source destinations and bounded bytes/deadlines.

Session duration ≤4days, cycles/attempts ≤10000, paper commands ≤1000, canonical
journal evidence ≤512MiB and bounded event count. Each session supplies its own cap;
existing session configurations are never widened automatically. Creation validates
the declared/derived event-size budget and reserves a terminal capacity checkpoint.
Admission records a bounded STOPPED/capacity result before ordinary evidence can
exhaust that reserve;
evidence is never pruned, old balances never reset. These are engineering ceilings,
not validated production throughput or physical database disk quotas. CPU/disk sizing
must be measured for the actual M8B configuration. A finite worker invocation also
requires explicit cycles/seconds and activation flag; creating a session starts no IO.

## Recovery and operations

Recorded progress is not process liveness. Read-only dashboard/API shows checkpoint
status, receipt clock and pending attempts; source outage cannot appear as a healthy
feed merely because PostgreSQL responds. Database failures prevent further commits.
If the database cannot record an incident, safe process diagnostics and the previous
checkpoint remain; no always-on external incident delivery is claimed.

Backups are private bounded custom-format PostgreSQL archives with hashes/schema/count
manifest. Restore refuses any existing target and only creates the dedicated restore
validation database; no drop/overwrite. Restore does not change runtime hashes or
reinterpret historical results under new code. Stop writers for count comparison,
retain original images/configurations and follow document26. No automatic destructive
retention, automatic migration rollback or cloud backup service is introduced.

## Gates

M8A tests use synthetic frames/captures, virtual clocks and isolated PostgreSQL.
No real collection, model call or Telegram send is part of implementation acceptance.
M8B requires reviewed/shipped code, approved operational parameters and explicit local
multi-day start. M8C requires independent vendor/budget/security/deployment approval.


## Review repair amendment — approved 2026-10-23

User approved bounded supervised restart plus a readonly staleness probe, explicitly
retaining manual recovery for collection_failure and clock_gap and never clearing
risk latches. Docker health status alone does not restart a process; only failed
exits receive up to three restart attempts. Session deadline/attempt/cycle limits
remain authoritative across restarts. Probe progress is not proof of valid market
input. No deployed worker was started by this amendment.

User requested and explicitly approved the larger configurable512MiB canonical
journal ceiling. This is an engineering admission limit, not physical disk space or
a proven throughput claim. Actual database footprint and real-reducer timings must
be recorded from isolated benchmarks. Incremental recovery/capacity implementation
is undergoing verification; this document is not evidence that those checks passed.

Entry-only order-notional caps do not prohibit reducing sales or liquidation marks.
Other existing identity, depth, freshness, quantity, fee and execution-cost checks
remain applicable. Paper command submission time is ordered independently of the
simulated fill timestamp; risk attribution retains its execution UTC time.

Clarifications to the DEC-039 amendment: the first data tick after any detected gap (the gap
may be noticed on the worker's poll event or on the tick itself) is not counted. A duplicate
delivery neither counts nor resets progress, so a quiet market can still recover on three
distinct fresh events; recovery progress never exceeds three. A recovery notice is re-offered
idempotently when a worker starts, so a crash between the clear and its notice does not lose it.

Amendment (DEC-040, 2026-10-25): with the M8A collector no OBSERVED session can open a position,
so M8B is an infrastructure soak that makes no trading claim. Exit/settlement, an independent
forecast/feature source and session chaining are prerequisites only for a later run that can hold
positions; "qualified deterministic exits remain possible" describes the reducer's contract, not
an exercised path.
