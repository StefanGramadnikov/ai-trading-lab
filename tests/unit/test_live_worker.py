"""The unattended loop: alignment, bounded cycles, failure containment, graceful stop."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_trading_lab.live.errors import CycleErrors, LoopIntegrityError
from ai_trading_lab.live.worker import next_run, run_loop


def test_runs_just_after_each_quarter_hour_so_a_new_candle_has_closed() -> None:
    at = datetime(2026, 1, 10, 12, 7, 31, tzinfo=UTC)
    assert next_run(at, 900, 20) == datetime(2026, 1, 10, 12, 15, 20, tzinfo=UTC)
    on_boundary = datetime(2026, 1, 10, 12, 15, 0, tzinfo=UTC)
    assert next_run(on_boundary, 900, 20) == datetime(2026, 1, 10, 12, 15, 20, tzinfo=UTC)
    after = datetime(2026, 1, 10, 12, 15, 20, tzinfo=UTC)
    assert next_run(after, 900, 20) == datetime(2026, 1, 10, 12, 30, 20, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 10, 12, 0, 5, tzinfo=UTC)
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> bool:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        return False  # not asked to stop


@pytest.mark.asyncio
async def test_a_bounded_run_makes_exactly_that_many_cycles_and_sleeps_between() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []
    calls: list[Any] = []

    async def cycle() -> Any:
        calls.append(clock.now)
        return {"ok": True}

    done = await run_loop(
        cycle, cycles=3, interval=900, offset=20, clock=clock, sleep=clock.sleep, emit=events.append
    )
    assert (done.done, done.failed) == (3, 0) and len(calls) == 3
    assert len(clock.sleeps) == 2  # no sleep after the last cycle
    assert [e["event"] for e in events].count("cycle_done") == 3


@pytest.mark.asyncio
async def test_a_failing_cycle_is_contained_and_the_loop_keeps_going() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []
    attempts = 0

    async def flaky() -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise TimeoutError("venue timed out")
        return {"ok": True}

    done = await run_loop(
        flaky, cycles=4, interval=900, offset=20, clock=clock, sleep=clock.sleep, emit=events.append
    )
    assert (done.done, done.failed) == (4, 1) and attempts == 4
    failed = [e for e in events if e["event"] == "cycle_failed"]
    assert len(failed) == 1 and failed[0]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_repeated_failures_stop_the_loop_so_the_supervisor_can_restart_it() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []

    async def always() -> Any:
        raise OSError("down")

    with pytest.raises(RuntimeError, match="consecutive"):
        await run_loop(
            always,
            cycles=None,
            interval=900,
            offset=20,
            clock=clock,
            sleep=clock.sleep,
            emit=events.append,
            max_failures=3,
        )
    assert [e["event"] for e in events].count("cycle_failed") == 3


@pytest.mark.asyncio
async def test_a_stop_request_ends_the_loop_after_the_current_cycle() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []
    calls: list[Any] = []

    async def stop_after_first_sleep(seconds: float) -> bool:
        await clock.sleep(seconds)
        return True

    async def cycle() -> Any:
        calls.append(1)
        return {}

    done = await run_loop(
        cycle,
        cycles=None,
        interval=900,
        offset=20,
        clock=clock,
        sleep=stop_after_first_sleep,
        emit=events.append,
    )
    assert done.done == 1 and events[-1]["event"] == "stopped"


@pytest.mark.asyncio
async def test_unbounded_runs_must_be_explicit() -> None:
    clock = Clock()

    async def cycle() -> Any:
        return {}

    with pytest.raises(ValueError, match="cycles"):
        await run_loop(
            cycle, cycles=0, interval=900, offset=20, clock=clock, sleep=clock.sleep, emit=print
        )


@pytest.mark.asyncio
async def test_the_stop_aware_sleep_reports_a_stop_request_and_a_normal_timeout() -> None:
    import asyncio

    from ai_trading_lab.live.worker import stop_aware_sleep

    event = asyncio.Event()
    sleep = stop_aware_sleep(event)
    assert await sleep(0.01) is False
    event.set()
    assert await sleep(5) is True


@pytest.mark.asyncio
async def test_an_integrity_failure_stops_the_loop_at_once_with_no_retry() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []
    attempts = 0

    async def corrupted() -> Any:
        nonlocal attempts
        attempts += 1
        raise LoopIntegrityError("Idempotency key conflict")

    with pytest.raises(LoopIntegrityError):
        await run_loop(
            corrupted,
            cycles=5,
            interval=900,
            offset=20,
            clock=clock,
            sleep=clock.sleep,
            emit=events.append,
        )
    assert attempts == 1 and events[-1]["event"] == "fatal"


@pytest.mark.asyncio
async def test_a_lost_lock_stops_the_loop_before_another_cycle_can_write() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []
    cycles_run = 0

    async def cycle() -> Any:
        nonlocal cycles_run
        cycles_run += 1
        return {}

    beats = 0

    async def heartbeat() -> None:
        nonlocal beats
        beats += 1
        if beats == 2:
            raise LoopIntegrityError("lock lost")

    with pytest.raises(LoopIntegrityError):
        await run_loop(
            cycle,
            cycles=5,
            interval=900,
            offset=20,
            clock=clock,
            sleep=clock.sleep,
            emit=events.append,
            heartbeat=heartbeat,
        )
    assert cycles_run == 1  # the second cycle never ran


@pytest.mark.asyncio
async def test_strategy_errors_in_a_cycle_count_as_a_failed_cycle_but_are_contained() -> None:
    clock = Clock()
    events: list[dict[str, Any]] = []

    async def partial() -> Any:
        raise CycleErrors(["v0-benchmark:ValueError"])

    done = await run_loop(
        partial,
        cycles=2,
        interval=900,
        offset=20,
        clock=clock,
        sleep=clock.sleep,
        emit=events.append,
    )
    assert (done.done, done.failed) == (2, 2)
    assert events[0]["detail"] == ["v0-benchmark:ValueError"]


@pytest.mark.asyncio
async def test_unexpected_cycle_failures_and_integrity_failures_reach_the_error_hook() -> None:
    clock = Clock()
    seen: list[BaseException] = []

    async def hook(exc: BaseException) -> None:
        seen.append(exc)

    async def flaky() -> Any:
        raise TimeoutError("venue timed out")

    result = await run_loop(
        flaky,
        cycles=2,
        interval=900,
        offset=20,
        clock=clock,
        sleep=clock.sleep,
        emit=lambda _e: None,
        on_error=hook,
    )
    assert result.failed == 2 and [type(e) for e in seen] == [TimeoutError, TimeoutError]

    async def corrupt() -> Any:
        raise LoopIntegrityError("ledger clock went backwards")

    seen.clear()
    with pytest.raises(LoopIntegrityError):
        await run_loop(
            corrupt,
            cycles=3,
            interval=900,
            offset=20,
            clock=clock,
            sleep=clock.sleep,
            emit=lambda _e: None,
            on_error=hook,
        )
    assert [type(e) for e in seen] == [LoopIntegrityError]


@pytest.mark.asyncio
async def test_giving_up_after_repeated_failures_is_itself_reported_with_a_stack() -> None:
    clock = Clock()
    seen: list[BaseException] = []

    async def hook(exc: BaseException) -> None:
        seen.append(exc)

    async def always_fails() -> Any:
        raise TimeoutError("venue timed out")

    with pytest.raises(RuntimeError, match="consecutive failed cycles"):
        await run_loop(
            always_fails,
            cycles=None,
            interval=900,
            offset=20,
            clock=clock,
            sleep=clock.sleep,
            emit=lambda _e: None,
            on_error=hook,
            max_failures=3,
        )
    assert [type(e).__name__ for e in seen] == ["TimeoutError"] * 3 + ["RuntimeError"]
    assert seen[-1].__traceback__ is not None and isinstance(seen[-1].__cause__, TimeoutError)
