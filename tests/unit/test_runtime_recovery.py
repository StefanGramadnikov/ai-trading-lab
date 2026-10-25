"""DEC-039: bounded automatic recovery of collection_failure/clock_gap, nothing else."""

from datetime import timedelta
from typing import Any

from tests.backtest_helpers import research
from tests.unit.test_runtime_clock_boundaries import shifted_frame
from tests.unit.test_runtime_engine import config, control, reducer, tick

START = research().start_at_utc


def fresh(seconds: int, index: int | None = None) -> dict[str, Any]:
    return tick(shifted_frame(START + timedelta(seconds=seconds)), index or seconds)


def fail(seconds: int) -> dict[str, Any]:
    return control("failure", seconds, f"fail:{seconds}")


def run(cfg: dict[str, Any], *events: dict[str, Any]) -> dict[str, Any]:
    return reducer(cfg, events)


def test_failure_clears_only_after_three_consecutive_fresh_ticks() -> None:
    cfg = config()
    base = (fail(1), fresh(2), fresh(3))
    state = run(cfg, *base)
    assert state["status"] == "DEGRADED" and state["suspension_reason"] == "collection_failure"
    assert state["recovery_ticks"] == 2 and state["auto_clears"] == 0
    state = run(cfg, *base, fresh(4))
    assert state["status"] == "RUNNING" and state["suspension_reason"] is None
    assert state["recovery_ticks"] == 0 and state["auto_clears"] == 1
    assert state["entry_suspended"] is False


def test_a_new_failure_resets_recovery_progress() -> None:
    cfg = config()
    state = run(cfg, fail(1), fresh(2), fresh(3), fail(4), fresh(5), fresh(6))
    assert state["status"] == "DEGRADED" and state["recovery_ticks"] == 2
    assert (
        run(cfg, fail(1), fresh(2), fresh(3), fail(4), fresh(5), fresh(6), fresh(7))["status"]
        == "RUNNING"
    )


def test_duplicate_native_events_do_not_count() -> None:
    cfg = config()
    repeated = (fail(1), fresh(2), fresh(2, 99), fresh(2, 98), fresh(2, 97))
    state = run(cfg, *repeated)
    assert state["status"] == "DEGRADED" and state["recovery_ticks"] == 1


def test_gap_detecting_tick_is_not_counted_and_three_more_are_required() -> None:
    cfg = config()
    cfg["max_gap_seconds"] = 10
    gap = fresh(100)
    state = run(cfg, gap)
    assert state["suspension_reason"] == "clock_gap" and state["recovery_ticks"] == 0
    assert run(cfg, gap, fresh(101), fresh(102))["status"] == "DEGRADED"
    state = run(cfg, gap, fresh(101), fresh(102), fresh(103))
    assert state["status"] == "RUNNING" and state["auto_clears"] == 1


def test_a_second_gap_during_recovery_restarts_it() -> None:
    cfg = config()
    cfg["max_gap_seconds"] = 10
    state = run(cfg, fresh(100), fresh(101), fresh(102), fresh(200), fresh(201), fresh(202))
    assert state["status"] == "DEGRADED" and state["recovery_ticks"] == 2


def test_manual_pause_is_never_cleared_by_ticks_or_a_gap() -> None:
    cfg = config()
    cfg["max_gap_seconds"] = 10
    ticks = tuple(fresh(100 + n) for n in range(6))
    state = run(cfg, control("pause", 1, "pause"), *ticks)
    assert state["status"] == "PAUSED" and state["suspension_reason"] == "manual_pause"
    assert state["auto_clears"] == 0
    # An explicit resume still clears the pause, as before.
    assert run(cfg, control("pause", 1, "pause"), control("resume", 2, "resume"))["status"] == (
        "RUNNING"
    )


def test_risk_latch_survives_automatic_recovery() -> None:
    cfg = config()
    cfg["profile"]["drawdown_fraction"] = "0.001"
    cfg["profile"]["daily_loss_fraction"] = "0.001"
    from tests.unit.test_experiment_runner import tape_with_exit

    first, second, third = tape_with_exit()
    state = run(
        cfg, tick(first, 1), tick(second, 2), fail(10), fresh(11), fresh(12), fresh(13), fresh(14)
    )
    assert state["risk_latch"] in {"drawdown_limit", "daily_loss_limit"}
    assert state["auto_clears"] == 1 and state["entry_suspended"] is True
    assert state["suspension_reason"] == state["risk_latch"]
    _ = third


def test_stop_and_capacity_are_never_cleared() -> None:
    cfg = config()
    stopped = run(cfg, fail(1), control("stop", 2, "stop"))
    assert stopped["status"] == "STOPPED"
    cap = dict(
        key="cap", at=control("stop", 2)["at"], payload={"action": "stop", "reason": "capacity"}
    )
    state = run(cfg, fail(1), cap)
    assert state["status"] == "STOPPED" and state["suspension_reason"] == "capacity"


def recovered(cfg: dict[str, Any], start: int, gap: int = 100) -> tuple[dict[str, Any], ...]:
    """One failure followed by three fresh ticks, all within a short span."""
    return (fail(start), fresh(start + 1), fresh(start + 2), fresh(start + 3))


def test_breaker_stops_automatic_clearing_after_five_per_session() -> None:
    cfg = config()
    events: list[dict[str, Any]] = []
    # Six episodes spaced 2 hours apart so only the per-session cap can bind.
    for episode in range(6):
        events.extend(recovered(cfg, 10_000 * (episode + 1)))
        state = run(cfg, *events)
        expected_cleared = episode < 5
        assert (state["status"] == "RUNNING") is expected_cleared, episode
    assert state["auto_clears"] == 5 and state["auto_clear_breaker"] is True
    assert state["suspension_reason"] == "collection_failure"
    # Only an explicit resume clears it after the breaker has opened.
    resume = control("resume", 10_000 * 7, "manual-resume")
    assert run(cfg, *events, resume)["status"] == "RUNNING"


def test_breaker_stops_a_flapping_source_within_one_hour() -> None:
    cfg = config()
    events: list[dict[str, Any]] = []
    for episode in range(4):  # four episodes inside one hour
        events.extend(recovered(cfg, 100 * (episode + 1)))
    state = run(cfg, *events)
    assert state["auto_clears"] == 3 and state["auto_clear_breaker"] is True
    assert state["status"] == "DEGRADED"


def test_gap_noticed_on_a_poll_event_skips_the_next_data_tick() -> None:
    """The real worker trips the gap on `poll`; the following tick is not recovery evidence."""
    cfg = config()
    cfg["max_gap_seconds"] = 10
    poll = dict(key="poll:1", at=fresh(100)["at"], payload={"action": "poll"})
    state = run(cfg, poll)
    assert state["suspension_reason"] == "clock_gap" and state["recovery_ticks"] == 0
    three = (fresh(101), fresh(102), fresh(103))
    assert run(cfg, poll, *three)["status"] == "DEGRADED"
    assert run(cfg, poll, *three, fresh(104))["status"] == "RUNNING"


def test_a_duplicate_between_fresh_events_neither_counts_nor_resets() -> None:
    cfg = config()
    state = run(cfg, fail(1), fresh(2), fresh(2, 90), fresh(3), fresh(3, 91), fresh(4))
    assert state["status"] == "RUNNING" and state["auto_clears"] == 1


def test_recovery_progress_is_capped_while_the_breaker_is_open() -> None:
    cfg = config()
    events: list[dict[str, Any]] = []
    for episode in range(4):
        events.extend(recovered(cfg, 100 * (episode + 1)))
    state = run(cfg, *events, fresh(450), fresh(451), fresh(452), fresh(453))
    assert state["auto_clear_breaker"] is True and state["status"] == "DEGRADED"
    assert state["recovery_ticks"] == 3


def test_breaker_window_boundary_is_exactly_one_hour() -> None:
    cfg = config()
    base: list[dict[str, Any]] = []
    for start in (100, 200, 300):  # three clears at 103, 203, 303
        base.extend(recovered(cfg, start))
    # A fourth episode finishing 3599 s after the first clear is still blocked...
    blocked = run(cfg, *base, *recovered(cfg, 103 + 3599 - 3))
    assert blocked["auto_clears"] == 3 and blocked["status"] == "DEGRADED"
    # ...but once the first clear is a full 3600 s old only two remain in the window.
    allowed = run(cfg, *base, *recovered(cfg, 103 + 3600 - 3))
    assert allowed["auto_clears"] == 4 and allowed["status"] == "RUNNING"


def test_failure_and_gap_while_paused_or_capped_keep_the_hold() -> None:
    cfg = config()
    cfg["max_gap_seconds"] = 10
    paused = run(cfg, control("pause", 1, "pause"), fail(2))
    assert paused["status"] == "PAUSED" and paused["suspension_reason"] == "manual_pause"
    gapped = run(cfg, control("pause", 1, "pause"), fresh(100))
    assert gapped["suspension_reason"] == "manual_pause"
    cap = dict(
        key="cap", at=control("stop", 2)["at"], payload={"action": "stop", "reason": "capacity"}
    )
    assert run(cfg, fail(1), cap)["suspension_reason"] == "capacity"


def test_non_advancing_event_time_resets_recovery() -> None:
    cfg = config()
    older = shifted_frame(START + timedelta(seconds=6))
    older = older.model_copy(
        update={
            "quote": older.quote.model_copy(update={"event_time_utc": START + timedelta(seconds=3)})
        }
    )
    state = run(cfg, fail(1), fresh(2), fresh(5), tick(older, 6))
    assert state["status"] == "DEGRADED" and state["recovery_ticks"] == 0


def test_new_state_fields_are_identical_in_compact_and_full_reducers() -> None:
    from ai_trading_lab.runtime.engine import RuntimeReducer

    cfg = config()
    events = (fail(1), fresh(2), fresh(3), fresh(4), fail(10), fresh(11))
    compact, full = RuntimeReducer(cfg, compact=True), RuntimeReducer(cfg, compact=False)
    for event in events:
        a, b = compact.apply(event), full.apply(event)
        for name in ("recovery_ticks", "auto_clears", "auto_clear_breaker", "auto_clear_times"):
            assert a[name] == b[name]
