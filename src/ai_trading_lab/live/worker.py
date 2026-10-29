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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.live.errors import LoopIntegrityError

LOCK_KEY = int.from_bytes(hashlib.sha256(b"ai-trading-lab/live-loop").digest()[:8], "big") - (
    1 << 63
)


class AlreadyRunning(RuntimeError):
    """Another live loop holds the single-writer lock."""


class Lease:
    """The held single-writer lock. `verify` proves the lock is still ours before each cycle."""

    def __init__(self, connection: AsyncConnection, backend_pid: int) -> None:
        self._connection = connection
        self.backend_pid = backend_pid

    async def verify(self) -> None:
        unsigned = LOCK_KEY & 0xFFFFFFFFFFFFFFFF
        try:
            held = await self._connection.scalar(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid() AND objsubid = 1 "
                    "AND classid::bigint = :hi AND objid::bigint = :lo"
                ),
                {"hi": unsigned >> 32, "lo": unsigned & 0xFFFFFFFF},
            )
        except Exception as exc:
            raise LoopIntegrityError("live loop lock connection lost") from exc
        if not held:
            raise LoopIntegrityError("live loop lock is no longer held")


@asynccontextmanager
async def single_instance(engine: AsyncEngine) -> AsyncIterator[Lease]:
    """Hold a PostgreSQL session lock on a dedicated autocommit connection for the loop's life."""
    connection = await engine.connect()
    try:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        if not acquired:
            raise AlreadyRunning("another live loop is already running")
        pid = int(await connection.scalar(text("SELECT pg_backend_pid()")) or 0)
        try:
            yield Lease(connection, pid)
        finally:
            try:
                await connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            except Exception:  # a dead connection already released it; never mask the real error
                pass
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


@dataclass(frozen=True)
class LoopResult:
    done: int
    failed: int


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
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    on_error: Callable[[BaseException], Awaitable[None]] | None = None,
) -> LoopResult:
    """Run `cycles` cycles (None means until stopped), aligned to the interval.

    `sleep` returns True when a stop was requested. The first cycle runs immediately. `heartbeat`
    runs before every cycle and proves the single-writer lock is still held. An integrity failure
    (corruption, a second writer, a lost lock) stops the loop at once and is never retried.
    """
    if cycles is not None and cycles < 1:
        raise ValueError("cycles must be a positive count, or None to run until stopped")
    done = failed = failed_in_a_row = 0
    while cycles is None or done < cycles:
        try:
            if heartbeat is not None:
                await heartbeat()
            report = await cycle()
        except LoopIntegrityError as exc:
            emit({"event": "fatal", "error": str(exc)[:200]})
            if on_error is not None:
                await on_error(exc)
            raise
        except Exception as exc:  # contained by design; the next cycle starts from durable facts
            failed += 1
            failed_in_a_row += 1
            event: dict[str, Any] = {
                "event": "cycle_failed",
                "error": type(exc).__name__,
                "in_a_row": failed_in_a_row,
            }
            if (detail := getattr(exc, "detail", None)) is not None:
                event["detail"] = detail
            emit(event)
            if on_error is not None:
                await on_error(exc)
            if failed_in_a_row >= max_failures:
                giving_up = RuntimeError(f"{failed_in_a_row} consecutive failed cycles")
                if on_error is not None:
                    try:
                        raise giving_up from exc  # raised once so the journal gets a stack to file
                    except RuntimeError as caught:
                        await on_error(caught)
                raise giving_up from exc
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
    return LoopResult(done=done, failed=failed)


def stop_aware_sleep(stop: asyncio.Event) -> Callable[[float], Awaitable[bool]]:
    async def sleep(seconds: float) -> bool:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    return sleep


__all__ = [
    "AlreadyRunning",
    "Lease",
    "LoopResult",
    "next_run",
    "run_loop",
    "single_instance",
    "stop_aware_sleep",
]
