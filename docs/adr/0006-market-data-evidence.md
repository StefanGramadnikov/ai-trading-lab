# ADR-0006 — Bounded read-only market data and replay evidence

- **Status:** Implemented under the approved M2 plan; local checks/review passed; shipped in PR #3, merge pending
- **Date:** 2026-10-18
- **Authorization:** DEC-031; no earlier architectural decision is superseded.

## Decision

Reuse the modular Python monolith and PostgreSQL. Promote the existing locked
httpx2 dependency to runtime for a strictly allowlisted public read transport.
Only DexScreener reads, Solana getTokenSupply/getTokenLargestAccounts, Polymarket
market/book reads and Kalshi market/book reads are supported. No arbitrary URL,
redirect, wallet, credential or order request is accepted. Collection is explicit,
bounded and opt-in via `atl-data`; no scheduler/new infrastructure is added.

Use immutable captures, native instrument identity and content-addressed definition
versions. Preserve exact raw text and hash, UTC receipt time, source event time when
known, schema version and OBSERVED/FIXTURE/REPLAY mode. JSON fractional numbers use
Decimal; normalized values serialize as decimal strings and PostgreSQL NUMERIC.
Do not substitute receipt/pair creation time for observation event time.

Persist complete batches atomically through ObservationStore. A capture UUID
identifies one receipt; duplicate delivery cannot rewrite evidence. A distinct poll
has a distinct capture even when its body matches. Definitions are content-hashed,
snapshots keyed by capture + definition + schema, and run/failure records append.
Application writes are append-only; owner-level database permissions are not locked.

Replay is strict ordered request matching with no public-network fallback. A stable
replay capture UUID refers back to its origin; receipt time remains historical.
Recursively frozen JSON containers prevent metadata/request/facts mutation through
ordinary APIs. Source descriptions remain untrusted research text.

## Consequences and limits

Source failures survive alongside partial successes. Unknown/stale/future event
times remain explicit quality states. Data is not a trade proposal or executable
quote. DexScreener discovery is sampling; Solana largest accounts are not holders;
Kalshi complementary asks are derived; Polymarket collateral identity is not assumed.

Synthetic deterministic contracts and real PostgreSQL tests are separate from
opt-in public smoke evidence. Sources may be blocked/rate-limited; there is no
credential/paid fallback. Retention, continuous ingestion, realistic execution,
financial accounting, risk policy and AI remain later milestones.

## References

- [Approved M2 plan](../18-m2-market-data.md)
- [Source register and observed limits](../16-source-register.md)
- [Testing standards](../08-testing-standards.md)

Amendment (2026-10-25, user-directed hardening after a reachability diagnosis; see document 16): an
OBSERVED `HttpTransport` (one that owns its HTTP client) only talks to the public internet. Before each
attempt it resolves the fixed host and refuses with `unsafe_destination` if any address is loopback, private,
link-local, carrier-grade NAT, documentation, reserved, multicast or malformed (IPv4-mapped IPv6 included);
after connecting it verifies the actual peer address of the response and discards the response, retaining no
body, if it is not public or unknown. A refused destination is permanent for the call (no retry) and consumes
no request budget. FIXTURE transports with injected clients are unaffected. This protects the OBSERVED label
from DNS overrides, local proxies and TLS-trusted impersonators; certificate verification remains the first
line of defence and is not weakened. Provider (AI) and Telegram clients, which carry credentials to fixed
origins, are not yet covered by this guard.
