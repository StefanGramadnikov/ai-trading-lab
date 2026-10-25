"""The unattended loop: a single-instance lock, quarter-hour alignment, contained failures.

The loop never trades faster than its configured cycle, runs a bounded number of cycles unless
told explicitly to run until stopped, contains a failing cycle (the engine's own decisions are
durable, so the next cycle starts from facts), and exits non-zero after repeated failures so a
supervisor restarts it from a clean process.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LOCK_KEY = int.from_bytes(hashlib.sha256(b"ai-trading-lab/live-loop").digest()[:8], "big") - (
    1 << 63
)


class AlreadyRunning(RuntimeError):
    """Another live loop holds the single-writer lock."""


@asynccontextmanager
async def single_instance(engine: AsyncEngine) -> AsyncIterator[None]:
    """Hold a PostgreSQL session lock on a dedicated connection for the life of the loop."""
    connection = await engine.connect()
    try:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        if not acquired:
            raise AlreadyRunning("another live loop is already running")
        try:
            yield
        finally:
            await connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
    finally:
        await connection.close()


def next_run(now: datetime, interval: int, offset: int) -> datetime:
    """The next multiple of `interval` seconds, plus `offset` so a closing candle is available."""
    epoch = int(now.timestamp())
    boundary = (epoch // interval) * interval
    candidate = boundary + offset
    if candidate <= epoch:
        candidate += interval
    return datetime.fromtimestamp(candidate, UTC)


async def run_loop(
    cycle: Callable[[], Awaitable[Any]],
    *,
    cycles: int | None,
    interval: int,
    offset: int,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[bool]],
    emit: Callable[[dict[str, Any]], None],
    max_failures: int = 10,
) -> int:
    """Run `cycles` cycles (None means until stopped), aligned to the interval.

    `sleep` returns True when a stop was requested. The first cycle runs immediately.
    """
    if cycles is not None and cycles < 1:
        raise ValueError("cycles must be a positive count, or None to run until stopped")
    done = failed_in_a_row = 0
    while cycles is None or done < cycles:
        try:
            report = await cycle()
        except Exception as exc:  # contained by design; the next cycle starts from durable facts
            failed_in_a_row += 1
            emit(
                {"event": "cycle_failed", "error": type(exc).__name__, "in_a_row": failed_in_a_row}
            )
            if failed_in_a_row >= max_failures:
                raise RuntimeError(f"{failed_in_a_row} consecutive failed cycles") from exc
        else:
            failed_in_a_row = 0
            emit({"event": "cycle_done", "report": report})
        done += 1
        if cycles is not None and done >= cycles:
            break
        wait = (next_run(clock(), interval, offset) - clock()).total_seconds()
        if await sleep(max(0.0, wait)):
            emit({"event": "stopped", "cycles": done})
            break
    return done


def stop_aware_sleep(stop: asyncio.Event) -> Callable[[float], Awaitable[bool]]:
    async def sleep(seconds: float) -> bool:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    return sleep


__all__ = ["AlreadyRunning", "next_run", "run_loop", "single_instance", "stop_aware_sleep"]
