# 18 — M2 market-data implementation plan

**Authorized:** 2026-10-18, user: “I approve. I also merged it already, you can
continue to the next phase.” Baseline: merged PR #2, `aba5f5d`. Scope is the M2
market-data plan previously presented in chat; no execution, paid providers or AI.

## Contract and increments

1. Define immutable typed captures, instruments, observations and explicit quality/
   provenance. Decimal values/units and UTC event/receipt timestamps stay distinct.
2. Add read-only allowlisted HTTP/RPC transport with request/body/time/concurrency/
   response limits, retry/backoff and source health. Inject clock, sleep and HTTP
   transport for deterministic contract tests. No arbitrary URLs/RPC methods.
3. Implement DexScreener token-profile sampling/pairs; Solana supply/largest token
   accounts; Polymarket metadata/outcome books; Kalshi metadata/YES-NO books.
   Discovery is bounded sampling, never exhaustive launch coverage. Missing source
   time is unknown/partial, not invented from receipt or pair creation time.
4. Persist append-only market-definition versions, raw captures and normalized
   observations in PostgreSQL with migration `0002_market_data`. Duplicate capture
   delivery is idempotent; separate identical-price polls remain distinct evidence.
   Store ingestion failures/runs without pretending failed sources were empty.
5. Provide bounded opt-in CLI collection and strict offline replay of captured
   responses. Data mode distinguishes OBSERVED, FIXTURE and REPLAY. No default
   background scheduler; no new Compose services.
6. Verify focused unit/contract/integration tests and the complete `make check`,
   migration/container smoke, bounded public-read smoke and independent review.
   Public endpoints may be inaccessible; record actual results and do not bypass
   restrictions or request credentials. Leave changes local until `/ship`.

## Interfaces and ownership

`market_data/models.py` defines shared immutable models. Transport protocol:
`request(source, method, path, *, params=None, json_body=None) -> Capture`.
`Capture.payload()` parses JSON numbers without float conversion. Transport errors
carry sanitized code and an optional capture; adapters return a `Batch` with
captures, observations and failures, preserving partial evidence.

Adapters implement `collect(..., limit=...) -> Batch`. Dex/Solana accept bounded
mint addresses; Polymarket/Kalshi sample open markets. Source-specific metadata and
outcome IDs must match book identity; Kalshi complementary asks are marked derived.
Replay supplies the same transport interface without networking and preserves
captured times/provenance. Root owns models, storage/migration, runner/CLI and docs;
independent subtasks own transport and individual adapters plus their tests.

## Acceptance and failure cases

- Invalid identities, non-finite/negative amounts, currency/unit ambiguity, malformed
  JSON, timestamp skew/missingness, empty books, partial pages, 429/5xx and timeouts.
- Exact Decimal round trips, immutable metadata versions, FK integrity, transaction
  rollback, duplicate delivery versus new poll, and deterministic offline replay.
- Hard collection/page/request/time limits; read-only allowlist cannot be widened
  by API descriptions, redirects, RPC payloads or replay files.
- Existing health endpoints and PAPER_ONLY validation stay compatible; database
  connectivity is never described as trading readiness.
- Local/fixture/observed evidence reported separately; no claims of legal eligibility,
  realistic fills, complete discovery, unique holders or financial performance.


## Local verification handoff

Implemented the six increments on `feature/m2-market-data`. Full gate: 234 tests,
95.80% coverage, Ruff/mypy PASS. Review repair round 1 resolved three P2 findings;
affected independent re-review PASS. Production container/migration/health and
schema drift checks PASS. Public-source partial/failure evidence is in documents
13/16; it does not establish continuous availability or executable quotes.

Shipping/hosted CI remains pending `/ship`; M3 is not authorized. No staging,
commit, push, PR or merge was performed for M2 during implementation.


The explicit subsequent ship invocation found and repaired excessive JSON nesting
(raw captures and Polymarket JSON-encoded arrays). Parsing now enforces 64-container
depth and returns handled validation failures; prior observations remain available.
Post-repair full gate: 240 tests, 96.00% coverage, Ruff/mypy PASS. See document 13
for shipping review/CI evidence and the distinct earlier development results.


## Shipping handoff

The user explicitly invoked the ship skill. Implementation `625b5b0` is pushed in
[PR #3](https://github.com/StefanGramadnikov/ai-trading-lab/pull/3); independent
shipping repair re-review PASS and implementation push CI PASS. Earlier statements
about uncommitted/pending shipping describe the development snapshot. Final-head
checks and merge status belong to that PR; no merge or M3 approval is implied.
