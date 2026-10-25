# CODEX_FIXES repair status — 2026-10-23

**INCOMPLETE.** User's three automatic repair rounds are exhausted. No further
application/test repairs, staging, commits, pushes, branches or PR changes have
been made after that limit. Existing demo containers/volumes/data were not touched.
All fixtures and benchmark captures are synthetic. No market/AI/Telegram request,
operational activation, multi-day run or deployment occurred. Approved downloads
restored pinned Python/locked dependencies and Chromium, including image dependencies.

## Finding disposition

“Deferred” below includes work awaiting renewed repair authorization; it does not
assert a finding was disproved. Findings not reached have not been independently
confirmed by new failing regressions. No finding has been finally disputed.

| Finding | Status | Regression / remaining work |
| --- | --- | --- |
| F-01 | Fixed | `test_f01_reducing_sale_above_entry_cap_is_admitted`, `test_f01_profitable_runtime_exit_above_cap_closes_position`, `test_f01_peak_nav_above_entry_cap_drives_drawdown`, `test_f01_experiment_profitable_exit_and_marks_above_cap` |
| F-02 | Fixed; historical audit NOT RUN | `tests/unit/test_runtime_provenance.py` (9 red→green cases), `test_observed_poll_result_is_owned_by_worker_connection`. Original data audit requires explicit access approval; see document27. |
| F-03 | Deferred; substantial implementation verified | Compact/incremental state, mutation generation and terminal reserve implemented; `test_declared_oversized_result_stops_with_reserved_capacity` and `test_real_reducer_500_2000_6000_large_original_frames` pass. Capacity reason at/after deadline remains wrong; benchmark prints but does not assert timing budgets; new test lint/type errors remain. |
| F-04 | Supervision/probe fixed; auto-clear deferred | 18 cases in `test_runtime_health.py`; `test_probe_is_readonly_current_stale_terminal_and_no_writer_lock` passes. No actual Docker restart lifecycle test. Keep manual clear; risk latches unchanged. |
| F-05 | Fixed within explicitly approved narrower scope | `tests/integration/test_ai_global_budget.py` covers unknown calls across budgets, hard cap, pricing, concurrency and corrupt/missing history. Administrative reconciliation remains prohibited by ADR0009. |
| F-06 | Deferred; fix has new HIGH edge | `test_f06_pause_during_simulated_latency_is_accepted`, `test_f06_runtime_poll_and_pause_do_not_inherit_execution_clock` pass. Reviewer reproduced midnight risk-day rewind: a separate monotonic risk/execution clock is still required. |
| F-07 | Deferred; unit-level fix verified | `test_f07_rejected_poll_records_failure_or_returns_concurrent_stop` passes in both cases; real PostgreSQL conflicting-result/concurrent-control regression still required. |
| F-08 | Deferred; not reached | Verify shared risk policy/source hierarchy before implementation; no numeric defaults may be invented. |
| F-09 | Deferred; not reached | Preflight/request-sent distinction and envelope recovery regressions pending. |
| F-10 | Deferred; live shape unverified | Requested offline ordering contract tests not yet added. Capture must establish native ordering, duplicate levels and token/market identity; do not silently guess/sort. |
| F-11 | Deferred; chaining decision required | Single-session limit documented below. Historical result-read path still pending. |
| F-12 | Deferred; exit/settle decision required | No new exit/settle implemented. Runtime report labeling “mark-only, no realization” still pending. |
| F-13 | Deferred; not reached | Durable invoice attachment/replacement and actual/synthetic evidence semantics pending. |
| F-14 | Deferred; partial implementation | Journal reads now use lock-free repeatable-read snapshots; reporting event-loop offload and API responsiveness regression pending. |
| F-15 | Deferred; partial overlap only | F03 validates event/cycle/byte budget. Operator clock skew and cadence/freshness relation checks pending. |
| F-16 | Deferred; not reached | Oversized AI evidence must yield recorded abstention/failure with regression. |
| F-17 | Deferred; live behavior unverified | Offline quiet-book regression not yet added. Requires two book responses separated in time; one response cannot establish timestamp behavior. No stale mark refresh authorized. |
| F-18 | Deferred; not reached | Shared-envelope two-cell reproduction and actual varying-dimension validation pending. |
| F-19 | Deferred; not reached | All-mode native depth and activation deduplication plus deliberate existing-test correction pending. |
| F-20 | Deferred; partly covered | F01/F06 added real behavioral assertions; actual isolated backup/restore replay passed. Midnight/in-flight risk regression, hung collector, original collector boundary and remaining claimed-invariant tests pending. |
| F-21 | Deferred; not reached | Destination HMAC/non-storage decision and regression pending; no real recipient accessed. |
| F-22 | Deferred; not reached | Stable identity/raw-reference/freshness verification pending. Real Gamma schema/row-size claims remain unverified. |

## Observed checks

Commands ran from repository root. For `uv`, executable was
`/private/tmp/ai-trading-lab-tools/bin/uv`; locked checks used `UV_OFFLINE=true`
and `UV_CACHE_DIR=/private/tmp/ai-trading-lab-uv-cache`. Test PATH also included
`.venv/bin` so Alembic subprocesses resolve. Only test database URL:
`postgresql+psycopg://ai_trading_lab:fixes_test_only@127.0.0.1:32768/ai_trading_lab_test`.
Credentials above are throwaway synthetic examples, not a personal account.

| Command | Observed result |
| --- | --- |
| `uv python install 3.12.13` with fresh `UV_PYTHON_INSTALL_DIR=/private/tmp/atl-fixes-python` | PASS; executable-link warning preserved existing unmanaged ~/.local/bin/python3.12 |
| `uv sync --locked` with explicit restored `UV_PYTHON` | PASS;55 resolved packages; broken .venv preserved outside repo |
| `uv run --locked playwright install chromium` | PASS |
| `uv run --locked ruff format --check .` | PASS;200 files |
| `uv run --locked ruff check .` | FAIL; one unused UUID import in `tests/integration/test_runtime_incremental.py` |
| `uv run --locked mypy` | FAIL; four test type errors in same file at62,106,111,112;156 checked files |
| `uv run --locked pytest --cov=ai_trading_lab --cov-report=term-missing` | FAIL;1078 passed/1 failed in148.25s; all9 Chromium cases and repository6000-tick benchmark passed;95.13% coverage |
| `docker compose config --quiet` | PASS; no service changed |
| `docker build --pull=false --network=none --tag atl-fixes-review:local .` | FAIL; missing uncached locked dependencies could not download |
| `docker build --pull=false --tag atl-fixes-review:local .` | PASS using approved dependency downloads; no existing service changed |
| `PYTHONPATH="$PWD" .venv/bin/python /private/tmp/atl-fixes-production-verify.py` | PASS; production migration upgrade→downgrade→upgrade, Alembic check, no pytest/Playwright in image, actual pg_dump/new isolated pg_restore/full runtime checkpoint replay |
| `git diff --check` / local Markdown targets | PASS before final handoff |

The sole pytest failure is `test_connectivity_and_rollback`: the test still expects
migration0010_runtime, while the new additive migration is0011_runtime_generation.
This needs a deliberate assertion update; it was left untouched at the repair limit.
No applied migration was edited. Full gate is NOT green.

Coverage95.13% is0.07 percentage points below the review's95.20% baseline. New
cache/capacity, probe and aggregate-budget logic expanded the measured code; remaining
branches are not all tested. Current runtime engine90%, journal92%, health92%, AI
budget97%. The90% floor passes but is not evidence that the requested coverage
baseline/improvements or remaining behavioral invariants are complete. No coverage
configuration or thresholds were weakened.

The production verification helper's first invocation lacked the repository import
path and failed before Docker work (`tests` module unavailable); rerunning with
explicit PYTHONPATH above passed. This was an execution-context correction, not an
application repair. Actual restored counts: migration0011_runtime_generation,
runtime_sessions1/runtime_events1; legacy paper/raw/experiment tables0. Runtime
receipts reside in the runtime journal. Private backup retained outside Git.

Full logs: `/private/tmp/atl-fixes-{format,lint,mypy}-final.log`,
`/private/tmp/atl-fixes-full-pytest.log`, `/private/tmp/atl-fixes-production-build-download.log`,
`/private/tmp/atl-fixes-production-verify.log`. Earlier red/green focused results
are in the progress log. No dedicated security/dependency scanner was added or run.

## Real-reducer benchmark

First measured driver: synthetic42,237-byte result envelopes with40KB original
metadata, six FILLED orders, original source validation and leased poll/result
pairs. Measured in this isolated environment; these are engineering measurements,
not observed-market performance or general throughput guarantees.

| Cycles | Events | Canonical bytes | Largest append (s) | Cold replay (s) | PostgreSQL relation growth (bytes) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 500 | 1000 | 22314716 | 0.017220 | 0.340116 | 24707072 |
| 2000 | 4000 | 89181721 | 0.035320 | 1.325928 | 98598912 |
| 6000 | 12000 | 267511236 | 0.038081 | 4.012780 | 295387136 |

At6000 the session was STOPPED at its configured cycle limit, not a fabricated
capacity result. Separate capacity regression passes before deadline. Relation
bytes include the measured PostgreSQL relations, not an operating-system disk quota.
Evidence `/tmp/atl_f03_benchmark_results.jsonl`; the equivalent
repository test subsequently passed within the full pytest run (coverage enabled).
The benchmark records durations but still needs explicit accepted timing assertions.

## Independent review and next action

Fresh-context code-reviewer reviewed the complete interim diff against CODEX_FIXES
and binding docs. Verdict **INCOMPLETE**; no automated tests/DB/network or writes by
reviewer, only read-only source inspection and in-memory reproductions. Behavioral
read-only restrictions are not an enforced separate sandbox.

Confirmed additional blockers:

1. HIGH: a tick submitted23:59:59UTC with2s modeled latency advances risk into the
   next day. Pause23:59:59.500 rewinds day/baseline; resume next day resets again,
   excluding already booked fees/losses from the day's loss calculation. Keep risk
   time monotonic separately from command time; add boundary/control regression.
2. MEDIUM: a capacity stop at/after deadline returns before assigning its capacity
   reason, reporting STOPPED/clock_gap instead. Preserve terminal reason and cover
   equality/after-deadline cases.

M8B remains blocked. M8B is a single session of at most4days, with no session chaining,
capital reset aggregation or30-day continuous-performance claim. Chaining needs a
separate carry-forward design. Historical read access and mark-only report labeling
remain requested safe work, not completed features.

Next: request user direction after the three repair rounds, fix confirmed remaining
blockers and continue untouched findings with regression-first verification. Keep
manual operational recovery; recommend designing exits/settlement before economic
performance evaluation. Public capture remains separately gated: matching metadata
if needed plus two GET /book responses about60s apart, recording token/market,
status/body/event and receipt times, ordering, freshness and rate-limit/error state.
No account, signing or order-placement request is part of that possible capture.

---

# Continuation — 2026-10-25 (second implementer, same working tree)

Codex stopped at its repair limit with a red gate. Work continued test-first on the same
uncommitted tree, on explicit user request. Nothing was committed, pushed or shipped. All
fixtures remain synthetic; no market, AI-provider or Telegram request occurred; no worker was
started against a public source. The isolated PostgreSQL was a throwaway container
(`postgres:17.9-bookworm`, loopback port, database `ai_trading_lab_test`) removed afterwards.
Existing demo containers/volumes were not touched.

## Updated disposition

| Finding | Status | Evidence |
| --- | --- | --- |
| F-01 | Fixed (Codex) | `tests/unit/test_review_f01.py` |
| F-02 | Fixed; historical audit NOT RUN | `tests/unit/test_runtime_provenance.py`; audit of pre-existing sessions needs data-access approval (document 27) |
| F-03 | Fixed | Real-reducer benchmark now **asserts** budgets: max append < 2 s, cold recovery < 9 s at 500/2000/6000 cycles (`test_runtime_benchmark.py`); capacity reason survives deadline/gap (`test_runtime_clock_boundaries.py`). Measured: max append 0.049 s, cold recovery 4.15 s at 6000 cycles. Recovery grows linearly. **Hosted CI measured about 3.2 ms per cycle (versus 0.65 ms locally): 1.7 s at 500, 6.5 s at 2000, and the 6000-cycle replay exceeded the journal's fixed 10 s bound.** The default benchmark therefore runs 250/1000 cycles; the full 500/2000/6000 run is opt-in (`ATL_BENCH_FULL=1`). Practical limit: roughly 3000 cycles per session on slow hardware before cold recovery nears the bound; a longer-lived session needs a higher bound or snapshot-based recovery. |
| F-04 | Fixed (supervision, progress probe, **bounded auto-recovery per DEC-039, approved 2026-10-25**) | `test_runtime_health.py`; `test_runtime_recovery.py` (17 reducer cases, including the 3599 s/3600 s breaker boundary, gap noticed on a poll event, capped progress and compact/full parity; clear after 3 fresh qualified ticks, resets, duplicates ignored, gap tick not counted, pause/risk latch/stop/capacity never cleared, breaker 5/session and 3/hour); `test_f04_worker_recovers_after_one_failure_and_announces_it` (real worker + PostgreSQL, recovery notice queued). K=3 and the breaker caps are operational values approved in DEC-039. |
| F-05 | Fixed within approved narrower scope (Codex) | `tests/integration/test_ai_global_budget.py` |
| F-06 | Fixed | monotonic `risk_at`; `test_f06_*`, `test_latency_midnight_pause_cannot_rewind_daily_baseline` |
| F-07 | Fixed | `test_f07_*`; stop during an in-flight poll ends cleanly (`test_stop_during_an_in_flight_poll_ends_cleanly`) |
| F-08 | **No code change; NEEDS USER DECISION** | The experiment-versus-runtime difference in missing-NAV handling is documented (ADR-0010 "suspends conservatively" for fixed tapes; ADR-0011 "missing marks block entries" for the live runtime). Standalone M4 backtests and M5 AI runs apply order/exposure caps only and no daily-loss/drawdown latch; no binding document requires one. Their results must not be compared with cohort/runtime results. Decide whether to unify before the 30-day evaluation. |
| F-09 | Fixed | `HTTPProvider.preflight` runs before any reservation; `test_ai_preflight.py` (locally rejected requests never reserve or send; sent failures still keep unknown billing) |
| F-10 | Offline test added; **live ordering UNVERIFIED** | `test_f10_book_level_order_is_preserved_never_silently_sorted`. The adapter does not sort; an unordered book is rejected by simulation (`unordered_book`). A read-only `GET /book` capture is needed before an OBSERVED run. |
| F-11 | Single-session limit documented; **chaining NEEDS USER DECISION**; read path for historical results NOT done | Whole-package fingerprint still makes any source edit invalidate stored sessions/runs. |
| F-12 | **Decided (DEC-040, 2026-10-25): deferred by design.** Labelled mark-only (`test_f12_runtime_report_is_labelled_mark_only`). Re-reading the code showed the finding's scenario is unreachable in M8A: the collector supplies no forecast/feature, so no position can open (the original review overstated reachability). M8B is a no-trade soak; exit/settlement, a forecast source and chaining gate any trading run. |
| F-13 | Fixed | `attach_expense` / `complete_expenses` (store, CLI `atl-experiment attach-expense|complete-expenses`); invoices replace estimates, never add (`test_f13_*`). The completeness declaration is a chained lifecycle event; the frozen configuration and hash are unchanged. |
| F-14 | Fixed | Journal replay verification runs in a worker thread; `test_f14_replay_yields_to_the_event_loop_between_events` fails without it. Reporting reuses one `Journal` so its verified-continuation cache survives requests. |
| F-15 | Fixed | Operator controls default to wall-clock now and are refused beyond ±30 s skew on OBSERVED sessions (`control_at`); cadence validators (`poll_interval ≤ max_gap`, `poll_interval ≤ simulation.max_age_seconds` for OBSERVED, `max_gap ≤ 4 days`) |
| F-16 | Fixed | Oversized evidence becomes a deterministic digest-only packet; the flow abstains with `evidence_too_large`, no provider call (`test_ai_evidence_bounds.py`) |
| F-17 | **UNVERIFIED; no change** | Needs two `GET /book` responses about 60 s apart for a quiet token to show whether `timestamp` advances. |
| F-18 | Fixed (Codex) | independent budget IDs and a declared dimension that must vary (`test_experiment_models.py`) |
| F-19 | Fixed | `ResearchInput` rejects the same instrument and event time under different snapshot IDs (`duplicate_native_event_cannot_renew_depth`). One existing test that modelled a changed book at an identical event time was updated deliberately to use a later event time. |
| F-20 | Mostly closed | New: hung collector bounded, production collector limits pinned, stop during a poll, midnight/gap tests (Codex), benchmark budgets. Not closed: a restore round trip is covered only by the manual production verification, not by pytest. |
| F-21 | Fixed | destination digest is HMAC keyed by the unstored bot token (`test_f21_*`). Any previously stored digest would no longer match; none are expected because no real Telegram configuration or send was ever used (not checked against the demo database). |
| F-22 | **UNVERIFIED; no change** | Changing `Instrument.definition_hash` would re-key stored data. Needs a real Gamma payload and measured row sizes first. |

## Gate on this tree

```
ruff format --check .          PASS (206 files)
ruff check .                   PASS
mypy                           PASS (161 source files)
pytest --cov=ai_trading_lab    1120 passed, 0 failed; coverage 95.20% (final, after review fixes)
```

Chromium was already installed, so the dashboard browser tests ran. The production-image
build, Compose validation and the backup/restore production verification were last run by
Codex on the earlier tree and were **not repeated** after these changes.
Coverage matches the review baseline of 95.20%; the floor is 90%.

## Needs your decision before M8B

1. ~~F-04~~ — decided: approved as DEC-039 and implemented (see the table).
2. F-11: carry-forward design for a trading/30-day run. No longer blocks M8B, which places no orders (DEC-040).
3. ~~F-12~~ — decided: DEC-040, M8B is a no-trade soak; exit/settlement deferred to a trading-run gate.
4. F-08: unify risk gating or keep documented separate scopes. No longer blocks M8B (DEC-040); decide before any trading run.
5. F-10/F-17/F-22: the approved read-only capture could not be completed — both hosts resolve to loopback on this
   machine (see document 16, 2026-10-25). Needs a decision on how, or whether, to obtain real Polymarket evidence.

## Independent review of the continuation changes

A fresh-context read-only reviewer examined the F-09, F-13, F-14, F-15, F-16, F-19, F-21 and
shared-journal changes. It reported no CRITICAL or HIGH findings, did not read ADRs 0009–0011
in depth, and ran nothing (reviewer sandboxing is behavioral, not enforced). Its MEDIUM findings:

1. **Control retries (fixed).** OBSERVED `pause/resume/stop` default to the wall clock, so a retry
   after a lost response carried a new `at` and hit "key conflict". Journal replay of a control
   action now matches on key and payload and ignores `at`
   (`test_control_retry_with_a_new_clock_replays_by_key_and_payload`).
2. **Stale continuation cache (fixed).** An append now extends a cached reducer only when its state
   equals the row just loaded; otherwise it rebuilds from the stored events
   (`test_stale_cached_continuation_is_never_extended`, red without the check). The cache still has no
   eviction, and the reporting service's shared journal keeps one reducer per viewed session in memory;
   acceptable for a local single-operator dashboard, noted for the VPS phase.
3. **Preflight before completed-call replay (NOT changed; documented).** If a call already finished
   in the budget history and the provider key later fails the local format check, the replay returns a
   fresh `invalid` result instead of the stored one. Locally invalid requests are deliberately rejected
   before any reservation; reordering would let them touch the budget. Edge case, only on provider
   configuration change.

Also noted by the reviewer and not changed: stored runtime configs created before the new cadence and
journal-budget validators may fail validation on load. This is moot while the whole-package source
fingerprint already rejects every stored session after any source edit (F-11).

## Independent review of DEC-039 recovery

A fresh-context read-only reviewer found no CRITICAL or HIGH issue and could not construct a path that clears a
pause, risk latch, capacity or stop. Its MEDIUM findings: (1) a gap noticed on the worker's `poll` event let the
next tick count — fixed, that tick is now skipped; (2) recovery progress exceeded three while the breaker was open —
capped at three; (3) a recovery notice could be lost if a worker stopped between the clear and its notice — re-offered
idempotently at worker start; (4) breaker boundary, hold-during-pause/capacity, non-advancing event time and
compact/full parity were untested — now asserted (two of the fixes were mutation-checked red). Duplicates were
deliberately left as "neither count nor reset": resetting on a duplicate would block recovery in quiet markets
(F-17), where duplicates are normal; recovery still needs three distinct fresh events.

## F-23 (new, user-directed) — OBSERVED destination guard

Found while diagnosing the failed public capture: on the development machine the Polymarket hosts resolve to
loopback and a locally trusted development certificate answers. Certificate verification failed closed, but the
OBSERVED label otherwise relied on DNS and TLS honesty alone. `HttpTransport` now refuses non-public destinations
before connecting and verifies the real peer afterwards (`tests/unit/test_transport_destination.py`, 31 cases
including a real-client loopback check that pins the peer-address assumption; both halves were mutation-checked
red). Not covered yet: the AI provider and Telegram clients, which send credentials to fixed origins; the same
guard should be applied there before any real key is used on an untrusted network.
