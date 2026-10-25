# CODEX_FIXES

Independent review of the repository requested by the user. Treat each finding as input to verify, not as authority: confirm it against the code and the binding docs (AGENTS.md source hierarchy). If a finding conflicts with docs/12-decisions.md or an ADR, stop and surface the conflict instead of fixing it. Fix confirmed findings in the order listed, using the test-first loop in AGENTS.md: add a failing regression test first, then fix. Do not refactor unrelated code or weaken tests. If you believe a finding is wrong, explain why instead of fixing it.

**Review basis:** static code reading (three read-only reviewers plus spot-checks by the lead reviewer). Afterwards the existing suite was run once in a throwaway container against an isolated PostgreSQL (see "Test run evidence" at the end): ruff format/check and mypy passed, 1013 tests passed, 9 browser e2e tests errored because the Playwright browser is not installed. A green suite does not contradict the findings: none of the regression tests proposed here exist yet, and F-19/F-20 describe tests that pass without asserting their claimed invariant. Line numbers refer to the working tree on branch `feature/m8-local-runtime` at review time. PASS/"verified" claims in docs/13-progress.md were treated as unverified.

**Verdict for M8B: FIX FIRST.** No CRITICAL findings. 5 HIGH, 17 MEDIUM. Do not start the multi-day OBSERVED run until F-01 to F-05 are fixed and F-11/F-12 have a user decision.

---

## CRITICAL

None found. Checked specifically: no order/wallet/signing/trading-credential client in `src/`, `compose.yaml`, `Dockerfile` or `.env.example`; `PAPER_ONLY` is a `Literal` and fails closed (`config.py:14`); AI output can only lower a buy limit the rules already proposed and never sees risk config; secrets are `SecretStr` and errors are generic strings; no float in money paths; writes are atomic and idempotent.

---

## HIGH

### F-01 HIGH Entry-only `max_order_notional` cap also blocks exits and liquidation marks
- **Where:** `src/ai_trading_lab/paper_execution/accounting.py:123-131` (cap checked before the sell branch); `paper_execution/simulation.py:111-112` and `:181-182`; `runtime/engine.py:47-75` (`_nav` calls `simulate`) and `:98-110` (sell sizes to the whole position); `backtesting/reporting.py:64-67`; `backtesting/runner.py:72-95`; `experiments/runner.py:124,218`; `experiments/models.py:57-62` (`RiskProfile.effective` forces `order_notional == max_order_notional`).
- **Violates:** DEC-022; ADR-0011 ("qualified deterministic exits remain possible", full-liquidation NAV drives drawdown); docs/06 (loss/drawdown including unrealized).
- **Problem:** If a position was sized at the cap (cap 10, buy 20 @ 0.50) and the bid rises to 0.60, the full exit (notional 12) is rejected with `max_order_notional`, and the liquidation mark for NAV is rejected the same way, so NAV is `None`. Then (a) the winning position cannot be closed, (b) `peak_nav` is never updated while above the cap, so a later drop is measured against a stale low peak and the drawdown latch fires late or not at all, (c) in experiments the cell latches `unavailable_nav` and `trading_net_pnl`/ROI become null. Losing exits still pass, so realized results are biased toward losers. All current tests exit at a loss or use a cap of 1000, so this is invisible.
- **Required fix:** Reducing sells and valuation marks are never subject to the new-order notional cap (the cap is an entry control). NAV valuation must not depend on order-entry limits. If a cost cap should still gate marks, make that an explicit separate rule.
- **Regression test:** (1) `test_runtime_engine.py`: buy at the cap, then a bid above entry with an exit proposal; assert the position closes and `nav` is not `None`. (2) Tape rising above the cap then falling; assert `peak_nav` tracked the high and the drawdown latch fires at the correct level. (3) `test_paper_accounting.py`: a sell with notional > `max_order_notional` returns `None` from `admit`. (4) Same scenario in `experiments/runner` asserting `nav` is not `None`.
- **Status:** CONFIRMED (code traced; lead reviewer re-read `accounting.py:123-131` and `simulation.py:111`).

### F-02 HIGH OBSERVED provenance can be forged through the `step` CLI and permissive defaults
- **Where:** `runtime/cli.py:69-77` (`step` accepts any tick file for any session); `runtime/engine.py:145-176` and `:270-274` (tick accepted without a pending poll, any key); `paper_execution/replay.py:72-84` (checks only `capture.data_mode == OBSERVED`); `market_data/models.py:112` (`Capture.data_mode` defaults to `OBSERVED`); `market_data/transport.py:157,313` (mode stamped from a constructor argument even with an injected client).
- **Violates:** AGENTS.md boundary 3 (no invented evidence); ADR-0011 and docs/25 ("never relabel fixture as observed"; single owner); ADR-0006.
- **Problem:** If anyone writes a JSON file with hand-built `metadata_capture`/`book_capture` and runs `atl-runtime step <observed-session> event.json --key k --at t`, it passes structural validation, the paper portfolio trades on fabricated depth, and the journal/dashboard record it as OBSERVED. It also bypasses the single-owner lease (the CLI takes no worker lease). The repo's own tests do exactly this with synthetic `entry_data()`. The 30-day GO/EXTEND/STOP decision could rest on invented evidence. It would be CRITICAL if any evaluated session ever used `step`.
- **Required fix:** For OBSERVED sessions, a tick is accepted only if a pending `poll:N` exists and the key is `poll:N:result`, which only the lease-holding worker writes. The CLI refuses `step` for OBSERVED sessions (pause/resume/stop remain allowed). `Capture.data_mode` has no default. Only a transport that owns its real HTTP client can stamp OBSERVED. Add a one-off check of existing OBSERVED sessions for `runtime_events.event_key` not matching `poll:%:result`.
- **Regression test:** Reducer test: OBSERVED tick with no pending poll, or with a wrong key, raises. CLI test: `step` on an OBSERVED session is refused. Model test: `Capture(...)` without `data_mode` fails validation. Transport test: a mock-client transport cannot produce OBSERVED.
- **Status:** CONFIRMED (code and existing test). Whether any real session is affected is UNVERIFIED.

### F-03 HIGH Journal cost grows with whole-tape size; byte accounting is quadratic; session can wedge with no way to stop
- **Where:** `runtime/journal.py:97-123` (`_checkpoint` sums every historical checkpoint into `journal_bytes`); `:174-269` (`_load` re-validates, rehashes and reduces the whole tape); `:320,331-343` (`append` reduces again; capacity refusal raises `ValueError`); `:94` (event slack but no byte headroom); `runtime/engine.py:126-131,321-349` (state embeds full `history` including each order's `source_entry` with raw captures up to 100 KB each, plus a growing `native_events` list, in every checkpoint); `notifications/store.py:19-26` (`bounded` = `asyncio.timeout(10)`); `runtime/worker.py:66,76,99` (appends outside any try).
- **Violates:** docs/25 and ADR-0011 (bounded 2-4 day run, up to 10000 cycles, 64 MiB, "stop admission at capacity; do not prune"); DEC-016; docs/09 gate 3.
- **Problem:** Each cycle does two appends, each reducing the entire tape (O(n) with raw-capture parsing) plus chain verification. Once one append exceeds 10 s the timeout rolls it back, the worker exits with code 2, `poll_pending` stays true, `restart: "no"` leaves it dead, and a restart fails the same way. `stop`/`pause` are also appends, so the session can never be stopped. Separately, every event row stores a checkpoint containing all prior orders' raw captures and the counter adds all checkpoints, so size grows roughly O(k x n) with k orders (and about 38 x n^2 bytes from `native_events` alone). At the cap every append, including `stop`, raises; open positions stay open and the session stays RUNNING with no halt notification.
- **Required fix:** Append/get cost bounded independent of history length (incremental reducer or verified snapshot checkpoint; full replay only at start or on demand). Checkpoints hold hashes/counts/references, not payload copies. Capacity accounting reserves headroom so a terminal STOPPED/halt event can always be written, and reaching capacity ends in a clean recorded STOPPED with reason `capacity`. `create` validates that `max_cycles` x expected event size fits `max_journal_bytes`.
- **Regression test:** Integration test using the REAL reducer with realistic ~30-50 KB `ReplayEntry` payloads: append N = 500/2000/6000 ticks plus several fills; assert each append stays under a budget (for example 2 s), `journal_bytes` grows roughly linearly, `stop` is still appendable at the cap, and the worker reaches STOPPED/capacity and exits 0. Existing capacity tests use a toy `reduce_total` and tiny payloads (`tests/integration/test_runtime_journal.py:84-135`).
- **Status:** structure CONFIRMED; magnitude UNVERIFIED (reviewers estimated a wedge within hours to a day at 60 s polling). Confirm by benchmarking `Journal.append` with the real reducer and printing elapsed time and `journal_bytes` per tick.

### F-04 HIGH A single transient failure or clock gap disables entries for the rest of the run, and any exception kills the worker with no restart
- **Where:** `runtime/engine.py:246-247` (clock gap), `:256-259` and `:265-267` (`collection_failure` cleared only by manual `resume`; a good tick never clears it); `runtime/worker.py:91-93` (`max_attempts=1`, no retry), `:76,99`; `compose.yaml:46` (`restart: "no"`, no healthcheck for the worker); `reporting/service.py:206` (heartbeat is "not a live process probe"); `runtime/cli.py:44` (`resume` needs hand-typed `--key`/`--at`).
- **Violates:** DEC-016 (2-4 unattended days); docs/09 gate 3; docs/06 (heartbeat/kill switch).
- **Problem:** One HTTP 5xx/timeout/429, a Wi-Fi blip, or a laptop sleep longer than `max_gap_seconds` blocks buys until a human runs `atl-runtime resume`; the worker keeps consuming cycles with no trades. A DB restart, Docker Desktop restart, `TimeoutError`, reducer `ValueError`, or backward clock step ends the worker and nothing relaunches it. The dashboard shows stale RUNNING and nothing alerts that the worker is dead, because notifications are only queued by the live worker. Fail-closed is correct, but the run cannot be unattended or comparable.
- **Required fix:** A config-bounded auto-recovery policy (for example N consecutive fresh good ticks clear `collection_failure` and `clock_gap`, never a risk latch). Supervised restart (compose `restart: on-failure` with a cap, or a wrapper that relaunches until the deadline). A worker staleness probe/healthcheck from `last_at` age vs `poll_interval`.
- **Regression test:** Reducer test: failure -> three fresh ticks -> entries allowed again while `risk_latch` stays latched. Worker integration test: a collector that fails once and the run still trades afterward. Existing `test_runtime_engine.py:114-129` asserts the current behavior as intended; update it deliberately.
- **Status:** CONFIRMED. The recovery policy itself may need a user decision if ADR-0011 intends manual resume. NEEDS USER DECISION on whether auto-clear is allowed; the supervision and healthcheck parts do not.

### F-05 HIGH AI spending ceiling is defined by the run file, so a new envelope bypasses it
- **Where:** `ai/run_models.py:15-51`; `ai/budget.py:307-357` (`_admit` counts only calls in the same `budget_id`), `:106-115` (`reserve_cost` only checks price snapshot age < 1 day); `config.py:25-28`.
- **Violates:** ADR-0009 ("never regenerate the envelope" to bypass an uncertain call); DEC-035 (US$1 total cap); AGENTS.md (configurable provider cost budgets).
- **Problem:** `budget_id`, `BudgetPolicy` limits and `PriceSnapshot` all come from operator-supplied JSON. If a call is left unresolved (unknown outcome) in envelope A, creating envelope B with a new UUID gives a fresh cap and ignores A's unresolved call. An understated tariff in the file under-reserves. The "unknown outcome blocks further calls" invariant therefore holds only within one envelope and as a convention.
- **Required fix:** A process-level hard ceiling (Settings-level) and a cross-envelope rule: any unresolved paid call for a provider blocks new reservations for that provider until resolved by an explicit command. Prices come from a pinned or reviewed table, not the run file.
- **Regression test:** Create envelope A, leave one real-provider call unresolved, create envelope B with a new UUID; assert B's real-provider `reserve` is refused. Assert the sum of reserved cost across envelopes cannot exceed the Settings ceiling. Assert a run-file price below the pinned table is rejected.
- **Status:** CONFIRMED by reading; not executed.

---

## MEDIUM

### F-06 MEDIUM Simulated execution latency moves the ledger clock ahead of control and poll events and crashes the worker
- **Where:** `paper_execution/history.py:117-118,138` (`last_at = max(at, executed_at)`, `executed_at` includes latency); `runtime/engine.py:231`; `runtime/models.py:28-29` (no relation between `latency_ms` and `poll_interval_seconds`); `runtime/worker.py:76-80,99-102` (appends outside the try); same mechanism in `PaperStore.pause`.
- **Violates:** none, correctness/availability (docs/06 kill-switch must stay available).
- **Problem:** With latency 2 s and a fill at T, `last_at` becomes T+2. A poll at T+1 (poll interval 1 s) or a manual `pause`/`resume`/`stop` at T+1 is refused ("clock cannot move backwards"/duplicate-key-or-clock conflict), the worker crashes, and safety controls are refused for up to `latency_ms` (max 60 s). Self-heals once wall clock passes T+2.
- **Required fix:** Order control and poll events on the event clock independent of simulated execution time, or validate `latency_ms < poll_interval x 1000` at create and handle the refusal in the worker.
- **Regression test:** Reducer tape with latency 2 s: a tick that fills, then a `pause` 1 s later must be accepted. Worker run with latency > poll interval does not crash.
- **Status:** CONFIRMED.

### F-07 MEDIUM Reducer rejections kill the worker without recording a failure
- **Where:** `runtime/journal.py:337` (reducer `ValueError`) propagating through `runtime/worker.py:99`; `runtime/engine.py:282` (`runtime_native_event_conflict`), `:244-245` (`runtime_session_stopped` when `stop` lands during a poll).
- **Violates:** docs/25 (bounded attempts, recorded outcomes).
- **Problem:** A provider response that repeats a book timestamp with different depth, or a `stop` issued from the CLI during an in-flight poll, makes the result append raise. The tick is never written, `poll_pending` stays true, and the worker exits with a generic error. Each restart consumes another attempt as `interrupted`, leaving no diagnostic.
- **Required fix:** Collector output that the reducer rejects is recorded as a `failure` event with a stable reason code and the loop continues (or exits cleanly for stop).
- **Regression test:** Collector returns a conflicting tick; assert a `failure` is recorded and the worker keeps running. `stop` during a poll ends the worker with exit 0.
- **Status:** CONFIRMED by code.

### F-08 MEDIUM Risk logic is duplicated across paths and some research paths have no loss/drawdown gate
- **Where:** `runtime/engine.py:213-226`; `experiments/runner.py:38-49,127`; `backtesting/runner.py:29-43`; `ai/run_store.py:203-286`.
- **Violates:** docs/03 (single `RiskEngine.evaluate`); docs/06 mandatory controls; ADR-0003 (comparable configurations).
- **Problem:** The experiments runner latches `unavailable_nav` permanently (`halted = halted or ...`) and skips the daily-loss check when NAV is `None`; the runtime treats missing NAV as transient and still evaluates daily loss. The standalone AI run and the backtest apply no daily-loss, drawdown or missing-NAV gate. The same "risk profile" therefore behaves differently per path, which undermines cross-path comparisons and any claim that limits protect AI runs.
- **Required fix:** One pure risk-state function (day rollover, latch, missing-NAV rules) used by the runtime, experiments, AI and backtest runs.
- **Regression test:** Feed one tape through all paths; assert identical latches and reason codes.
- **Status:** CONFIRMED. If the divergence is intentional, NEEDS USER DECISION and docs/ADR must say so.

### F-09 MEDIUM A local pre-flight failure after reservation permanently bricks the whole AI envelope
- **Where:** `ai/service.py:26-36`; `ai/providers.py:100-104,316-321`; `ai/budget.py:133-135,310-311`; `ai/run_store.py:233-241`; `ai/orchestration.py:42`.
- **Violates:** ADR-0009 (reservation lifecycle must be resolvable); FR for bounded AI cost.
- **Problem:** `reserve()` runs first; then `_body` can raise `input_budget_exceeded` (the limit is in tokens but the local check is UTF-8 bytes + 512), or a definitive 4xx, an unstripped-key illegal header, or the flow timeout cancels an in-flight call. The result has `usage=None`, which is treated as an unresolved call, and `_admit` then returns `unresolved_call` for every future call in the envelope. A pending call cannot be finished because `reserve` returns blocked for the same ID.
- **Required fix:** Build and validate the request body before reserving, or record no-request-sent as a zero-cost resolved result and release the reservation. Definitive non-billable 4xx responses must not hold the envelope. Genuine unknown outcomes (sent, no response) must still block (do not weaken F-05).
- **Regression test:** `InferenceService` + `HTTPProvider` with an oversize evidence payload; assert the next `reserve` succeeds. Separate test: a timeout after the request was sent still blocks.
- **Status:** CONFIRMED. Tests miss it because `FixtureProvider` has no size check.

### F-10 MEDIUM Polymarket book ordering is assumed, never validated against a live payload
- **Where:** `market_data/adapters/polymarket.py:76-84` (levels kept in server order); `paper_execution/simulation.py:133-136` and `strategies/rules.py:112-117` (require best-first); docs/16 records the public smoke as `network_error`.
- **Violates:** none (data integrity risk to M8B usefulness).
- **Problem:** If the real CLOB returns bids ascending and asks descending, every live book is rejected as `unordered_book`, and the OBSERVED session never trades. That is fail-closed but would waste the multi-day run. Alternatively an adapter that sorts silently could mask a malformed book, so decide explicitly.
- **Required fix:** Verify with one real `/book` response (read-only GET, user-approved) and either sort levels explicitly in the adapter or document and test the real order.
- **Regression test:** Contract fixture with ascending bids / descending asks yields a qualified, correctly ordered quote.
- **Status:** UNVERIFIED. Confirm with a single captured `/book` response.

### F-11 MEDIUM Hard run limits, whole-package source fingerprint and per-session funding make multi-session or 30-day evaluation costly
- **Where:** `runtime/models.py:43-44` (4-day cap); `runtime/journal.py:88-94,194` (cycle/byte caps; fingerprint checked on load); `backtesting/models.py:22-29` (`runtime_fingerprint` hashes every package `*.py`); `experiments/store.py:163`; `ai/run_store.py:108,184-189`; `backtesting/store.py:136`; `runtime/cli.py` `create`.
- **Violates:** DEC-016 (local 2-4 days then up to ~30 days); ADR-0011 ("No cross-period funding reset" holds only per `session_id`).
- **Problem:** Any source edit, even in an unrelated module, makes every existing session/run/experiment unreadable ("config or code integrity conflict"): `report`, `pause`, `stop` all call load, so a hotfix during a multi-day session kills it with no migration path. A 30-day run needs a chain of sessions, and `create` gives fresh `initial_cash` to any new UUID, so a funding reset between sessions is possible and unrecorded.
- **Required fix:** A versioned compatibility rule (pinned set of result-affecting modules) or a read-only path that keeps old results readable. An explicit session-chaining/carry-forward design, or a documented single-session limit for M8B.
- **Regression test:** Change an unrelated module's text; `report`/`show` still work. A second session on the same instrument either inherits the prior ending portfolio or is refused.
- **Status:** CONFIRMED (mechanism). NEEDS USER DECISION on chaining vs single-session M8B.

### F-12 MEDIUM The runtime has no exit, settle or unwind path
- **Where:** `runtime/engine.py:235-243` (allowed actions: tick/poll/pause/resume/stop/failure); `runtime/source.py:25-35` (market query `active=true, closed=false`); `strategies/rules.py:143-153` (sell only on a qualifying forecast, which the collector never supplies).
- **Violates:** docs/06 (predefined independent monitor / exits during breach); ADR-0011 ("qualified deterministic exits remain possible"); docs/03 ("never report final realized P&L for unresolved contracts" needs settlement states).
- **Problem:** A held prediction position can never be sold by the worker (no forecast) nor settled at resolution. When the market closes, polls fail identity validation and the session sits DEGRADED with NAV `None`. A risk latch blocks buys but cannot unwind. M8B/M8C results are therefore unrealized-only and hard to interpret.
- **Required fix:** Either implement a deterministic exit/settle/unwind path in the reducer, or label reports explicitly "mark-only; no realization" and exclude realization claims from the evaluation.
- **Regression test:** A held position plus a market-closed response yields a recorded settle or an explicit `unrealized_only` status.
- **Status:** CONFIRMED by code. NEEDS USER DECISION (docs/25 may defer this; if deferred, only the labeling part is required).

### F-13 MEDIUM Experiment expenses are frozen at creation, so real invoice replacement is unreachable
- **Where:** `experiments/runner.py:68` (hard-codes the estimate's `evidence="synthetic"`); `experiments/costs.py:178` (invoice must match that evidence); `experiments/models.py:121-127`; no append-expense command in `experiments/store.py` or `experiments/cli.py`.
- **Violates:** FR-14 (cost accounting, estimate replaced by invoice); ADR-0010.
- **Problem:** Real AI invoices cannot replace estimates after the fact, and `expenses_complete` is declared before the costs exist, so the 30-day profit-after-all-expenses decision (DEC-018) cannot be completed from real invoices.
- **Required fix:** A durable, append-only command to attach observed invoices to an experiment, counted once in place of the estimate.
- **Regression test:** Run an AI cell, then add an observed invoice for `ai:<call_id>`; the report counts it exactly once and `expenses_complete` transitions only then.
- **Status:** CONFIRMED by code reading.

### F-14 MEDIUM Reporting reads take the writer's advisory lock and replay in the API event loop
- **Where:** `runtime/journal.py:161-166` (`_lock` in read-only `get`); `reporting/service.py:209-211` (`runtime_detail` runs a full verified replay per request).
- **Violates:** docs/03 ("UI failures must not interrupt risk monitoring"); NFR on isolation of dashboard from worker.
- **Problem:** A dashboard click replays the whole journal while holding `pg_advisory_xact_lock`; the worker's `append` waits inside the 10 s `bounded` window and a timeout kills the worker. The synchronous replay also blocks the API event loop, so `/health/*` can exceed the compose healthcheck's 3 s timeout (`compose.yaml:31`).
- **Required fix:** Reads take no writer lock (a REPEATABLE READ snapshot suffices) and run off the event loop.
- **Regression test:** Hold a worker append in flight; a concurrent `get` does not block it. `/health/live` responds within its deadline during `runtime_detail`.
- **Status:** CONFIRMED structurally; impact depends on F-03.

### F-15 MEDIUM Operator-supplied `--at` and un-validated operational parameters can brick or distort a session
- **Where:** `runtime/cli.py:44,76` (hand-typed `--at`); `runtime/journal.py:329-330` (backwards refused); `runtime/engine.py:246-253`; `runtime/models.py:39-54`.
- **Violates:** docs/25 (bounded parameters).
- **Problem:** `pause --at 2030-...` raises `last_at` and every later worker event fails "clock moved backwards"; `--at` at or after `deadline_at_utc` stops the session; a far-future `resume` flags a spurious gap. `max_gap_seconds` below the poll interval flags `clock_gap` every cycle (never trades); 7 days disables gap detection; `poll_interval` > `simulation.max_age_seconds` makes NAV `None` between polls and queues a halt notification each cycle; `max_cycles` that cannot fit the journal cap fails mid-run.
- **Required fix:** Control events take a bounded wall-clock timestamp (default now, reject values more than a small skew from now). `create` rejects contradictory configs (state the required relations among interval, gap, max age, deadline, cycles and journal bytes).
- **Regression test:** `resume --at now+1d` is refused. Table tests for rejected parameter combinations.
- **Status:** CONFIRMED.

### F-16 MEDIUM An oversized evidence frame aborts the whole AI run or cell instead of abstaining
- **Where:** `ai/evidence.py:41-47`; `ai/models.py:25` (`Evidence.payload` max 16000 chars); raised from `ai/run_store.py:222` and `experiments/runner.py:145`.
- **Violates:** ADR-0009 (bounded failure handling).
- **Problem:** A deep book produces evidence above 16000 characters; the exception aborts the run, and an experiment `advance` errors repeatedly so the cohort stalls, potentially after earlier calls were already paid.
- **Required fix:** Truncate deterministically with provenance, or abstain with a recorded reason; never raise out of the loop.
- **Regression test:** A frame whose evidence exceeds the cap yields an abstain/failure record and the run continues.
- **Status:** CONFIRMED.

### F-17 MEDIUM Native-event duplicate suppression may starve marks and NAV in quiet markets
- **Where:** `runtime/evidence.py:10-25` (key on book `timestamp`); `runtime/engine.py:280-294` (repeated key = duplicate, marks not refreshed); `paper_execution/simulation.py:97-103` (quote stale after `max_age_seconds`).
- **Violates:** ADR-0011 (duplicates must not renew depth) vs. the need to keep marks current; docs/06 stale-data gate.
- **Problem:** If Polymarket reports an unchanged book with an unchanged `timestamp`, a held position's mark ages out, NAV becomes `None`, entries/exits are blocked and the drawdown latch is not evaluated, although the market is being observed.
- **Required fix:** Distinguish duplicate evidence (no depth renewal) from an unchanged-but-currently-observed state (mark freshness refreshed without renewing depth).
- **Regression test:** Two polls 60 s apart with the same book timestamp keep NAV defined (mark fresh) without renewing consumed depth.
- **Status:** UNVERIFIED. Confirm by fetching `/book` twice a minute apart for a quiet token and comparing `timestamp`.

### F-18 MEDIUM Shared `budget_id` can silently break cell pairing; `paired()` does not require a varying dimension
- **Where:** `experiments/models.py:104-153` (`paired`), `:140-142` (pops `budget_id` from the signature so sharing is allowed); `experiments/runner.py:185-192`; `ai/budget.py:314-315` (`clock_regression`).
- **Violates:** ADR-0003 and docs/07 (exactly one comparison dimension; paired windows).
- **Problem:** Two AI cells sharing a `budget_id` and running on tape-virtual time: the later cell sees an earlier-than-last timestamp, gets `clock_regression`, and latches `inference_failure`, so the cells are treated asymmetrically. Also a vacuous comparison where no declared dimension varies passes validation.
- **Required fix:** One envelope per cell (or monotonic envelope time handled per cell), and `paired()` rejects configs where the declared dimension does not vary or more than one varies.
- **Regression test:** Two AI cells on one budget either are rejected at creation or both complete symmetrically. A config with no varying dimension is rejected.
- **Status:** UNVERIFIED. Confirm with a two-AI-cell experiment on one budget.

### F-19 MEDIUM Depth renewal is enforced only for REPLAY manifests; a test claiming otherwise is vacuous
- **Where:** `backtesting/models.py:59-70` (checks only `snapshot_id` conflicts); `ai/run_store.py:212` and `experiments/runner.py:136` (activation dedupe hashes the quote including `snapshot_id`); `tests/unit/test_backtest_runner.py:34-46`.
- **Violates:** ADR-0008 (no depth renewal by duplicate deliveries); docs/03 (duplicate deliveries harmless).
- **Problem:** A FIXTURE tape can re-deliver the same book under a new `snapshot_id`, renewing consumable depth and re-triggering paid AI calls. The test meant to prove consumption is enforced passes because its second frame abstains for lack of edge, not because depth was consumed.
- **Required fix:** Enforce no-renewal by native event identity/content fingerprint for all data modes, not by `snapshot_id`.
- **Regression test:** Same book content delivered twice under different snapshot IDs must not allow a second fill from the same depth or a second AI activation. Rewrite `test_backtest_runner.py:34-46` so the strategy WOULD trade on the second frame.
- **Status:** CONFIRMED (mechanism and vacuous test).

### F-20 MEDIUM Tests pass without asserting invariants they claim to cover (critical areas)
- **Where / gap:**
  - `tests/unit/test_runtime_engine.py:87-111,207-215` and `tests/unit/test_experiment_runner.py:52-74`: "exits continue" only with losing exits (hides F-01); `:207-215` midnight-baseline test has no midnight crossing, equality boundary or non-UTC offset.
  - `tests/unit/test_paper_accounting.py:180-188`: "reducing exit despite limits" uses a tiny sell that never exceeds `max_order_notional`.
  - `tests/unit/test_paper_simulation.py:233`: "negative zero and float consumption" tests only Decimal `-0.0`; `:214-222` "precision overflow" asserts `max_order_notional`.
  - `tests/integration/test_runtime_journal.py:84-135`: capacity tests use a toy reducer.
  - Clock-gap blocking is never asserted (`grep clock_gap tests` finds nothing).
  - `observed_collector` (`runtime/worker.py:20-32`) is never executed; the hung-collector bound `asyncio.timeout(min(15, remaining))` is untested because test collectors raise instead of hanging.
  - `stop`/`pause` during an in-flight poll untested. `tests/.../test_backup.py` is entirely stubbed; no real dump/restore round trip.
- **Violates:** docs/08 and AGENTS.md (tests must assert the claimed invariant).
- **Required fix:** Add the behavioral assertions above (these overlap with the regression tests in F-01 to F-07). Tick after a gap larger than `max_gap_seconds` abstains with `clock_gap`; a tick at 23:59:59 then 00:00:01 UTC (and a +02:00 offset) rolls `day_open`; a collector that sleeps 60 s completes as `collection_unavailable` within about 15 s; a real `pg_dump`/restore into a fresh database round trip with row counts compared.
- **Regression test:** The tests listed above.
- **Status:** CONFIRMED by reading and grep.

### F-21 MEDIUM Notification recipient identity hash is unsalted
- **Where:** `notifications/telegram.py:36-40`; `notifications/store.py:152`.
- **Violates:** NFR on secrets/PII handling; docs/24 (the hash is described as protecting recipient identity).
- **Problem:** `sha256("bot_id:chat_id")` over roughly 10-digit numeric inputs is brute-forceable from any DB dump, backup or audit export, defeating its purpose.
- **Required fix:** A keyed hash (HMAC with a local secret not stored in the DB) or do not store the hash.
- **Regression test:** The same destination under different keys yields different stored hashes; the stored value cannot be reproduced from `bot_id:chat_id` alone.
- **Status:** CONFIRMED.

### F-22 MEDIUM Market storage amplification will make a multi-day observed collection large
- **Where:** `market_data/adapters/polymarket.py:176,219` (whole Gamma record in `Instrument.metadata`); `market_data/models.py:151-154` (`definition_hash` includes volatile metadata); `market_data/storage.py:141-153,168-203` (snapshot JSON plus raw envelope and body duplicate the payload); `reporting/service.py:240-256` (one global "latest observation" masks per-source staleness).
- **Violates:** none, structural cost (docs/09 storage and retention).
- **Problem:** If volatile Gamma fields (volume, liquidity, prices) change, each poll creates a new `markets` row and the `(definition_hash, received_at)` index stops being a usable time series, while the payload is stored two or three times. Growth could be significant over 2-4 days (and 30 days).
- **Required fix:** Hash only stable identity fields into `definition_hash`; store the raw payload once and reference it; show per-source freshness in reports.
- **Regression test:** Two polls with only volatile fields changed produce one `markets` row and two snapshots. Reports show per-source staleness.
- **Status:** UNVERIFIED. Confirm with one real Gamma payload and a per-row size measurement.

---

## Notes for Codex on areas with no findings
No critical issues found in: paper-only and secrets handling, AI isolation from risk and execution, Decimal and rounding direction, idempotency and recovery, single-owner advisory lock, journal hash chain and replay comparison, no re-funding on restart, risk-latch persistence across resume/restart, dashboard/API authentication (all routes behind router-level HTTP Basic, no mutating routes, no CORS, loopback publish), notification outbox (no send without explicit enablement plus command), backup/restore (refuses existing targets, 0700/0600 permissions), AI reserve-before-HTTP and unknown-outcome blocking within an envelope, experiment config immutability and estimate-versus-invoice logic.

Minor items outside the finding list: FastAPI `/docs` and `/openapi.json` are unauthenticated (routes only); compose falls back to `POSTGRES_PASSWORD=local_example_only` on a loopback-published port; `RiskProfile` fractions are bounded to (0,1] with no relation between daily-loss and drawdown limits (acceptable while numeric limits are unapproved).

Structural differences from docs/03 to reconcile in docs (not necessarily code): there is no single `RiskEngine.evaluate(intent, snapshot, state)`; tables `proposals`, `ai_decisions`, `risk_events`, `portfolio_snapshots`, `system_events` do not exist; `reserved_cash` is always 0 (RESERVED/RELEASED are labels on an atomic IOC); `OrderIntent` lacks experiment/strategy/worst-case-cost fields; the runtime journal replaces `orders`/`fills`/`ledger_entries` for the live session.

---

## Test run evidence
Host `.venv` is unusable: its base interpreter under `/private/tmp/ai-trading-lab-python/` has lost part of its standard library, so it aborts at startup; `uv` is not installed on the host. Codex should recreate it (`uv sync --locked`) before the fix loop.

Run instead in a throwaway container (image `atl-m8-validation-api:latest`, Python 3.12.13, `uv sync --locked` with dev group, repo copied from a read-only mount) against a new throwaway `postgres:17.9-bookworm` on a private Docker network with database `ai_trading_lab_test`. Existing containers and volumes were not touched; the throwaway container and network were removed afterwards.

| Check | Result |
|---|---|
| `ruff format --check .` | PASS (185 files formatted) |
| `ruff check .` | PASS |
| `mypy` | PASS (142 source files) |
| `pytest --cov=ai_trading_lab` | 1013 passed, 9 errors, total coverage 95.20% (gate 90%) |

The 9 errors are all in `tests/e2e/test_dashboard.py` (`BrowserType.launch: Executable doesn't exist`): the Playwright Chromium browser was not downloaded, so the dashboard browser tests are **NOT RUN** (dashboard auth conclusions rest on code and the non-browser API tests). The full `make check` gate was therefore not completed. Not repeated: the CI-style production image build and Compose validation. Coverage gaps relevant to findings: `runtime/worker.py` 85% (lines 21-32 = `observed_collector`, never executed), `runtime/engine.py` 89%, `runtime/models.py` 81%, `reporting/service.py` 88%, `operations/backup.py` 87% (lines 335-353 untested).

## Not reviewed
- **Findings were not reproduced by failing tests.** Every finding is from static reading; the regression tests proposed above were not written or run, so none has been seen to fail. Magnitudes in F-03 were not benchmarked.
- No network calls: live Polymarket/DexScreener/Kalshi/Solana payload shapes, ordering and timestamp behavior were not observed (F-10, F-17, F-22 depend on this).
- Magnitudes in F-03 (append latency, bytes per tick) were estimated, not benchmarked.
- Alembic migrations were only skimmed (0009/0010 read for runtime/notification tables); upgrade/downgrade round trips and DB constraints were not exercised.
- `market_data/adapters/solana.py` and `dexscreener.py`/`kalshi.py` received less scrutiny than Polymarket; the Solana strategy path and rules under `strategies/` were checked for look-ahead only.
- Dashboard static JS/HTML was checked for `innerHTML` and trace stripping, not tested in a browser; Playwright tests were not run.
- Docker/Compose runtime behavior, healthchecks, log rotation and actual `pg_dump`/`pg_restore` were not executed; backup tests are stubbed.
- `.env` was deliberately not opened; secret-handling conclusions rest on code and `.env.example` only.
- docs/04, 10, 11, 14-17 and the GRONK reference were not reviewed; docs/13-progress.md claims were not relied on.
- CI workflows (`.github/`) and `pre-commit` configuration were not reviewed.
