"""Finite observation-driven paper worker; invocation and all resource caps are explicit."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from ai_trading_lab.market_data.models import DataMode, TransportError
from ai_trading_lab.market_data.transport import HttpTransport, TransportLimits
from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.runtime.notifications import queue_checkpoint
from ai_trading_lab.runtime.source import collect_frame

Collector = Callable[[RuntimeConfig], Awaitable[dict[str, object]]]


async def observed_collector(config: RuntimeConfig) -> dict[str, object]:
    async with HttpTransport(
        limits=TransportLimits(
            max_requests=2,
            max_attempts=1,
            max_response_bytes=100_000,
            timeout_seconds=5,
            total_seconds=10,
            concurrency=1,
            min_interval_seconds=0.5,
        )
    ) as transport:
        return await collect_frame(config, transport)


async def run_worker(
    journal: Journal,
    session_id: UUID,
    *,
    cycles: int,
    seconds: int,
    collector: Collector = observed_collector,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    if type(cycles) is not int or not 1 <= cycles <= 10000:
        raise ValueError("Bounded cycle count required")
    if type(seconds) is not int or not 1 <= seconds <= 345600:
        raise ValueError("Bounded duration required")
    deadline = monotonic() + seconds
    async with journal.worker(session_id) as active:
        config = RuntimeConfig.model_validate(await active.config(session_id))
        if config.data_mode != DataMode.OBSERVED:
            raise ValueError("Use explicit fixture steps, not a wall-clock fixture worker")
        checkpoint = await active.get(session_id)
        await queue_checkpoint(journal.engine, checkpoint, clock())
        for _ in range(cycles):
            if monotonic() >= deadline:
                break
            state = cast(dict[str, Any], checkpoint["state"])
            if state["status"] == "STOPPED":
                break
            stamp = clock()
            # Never replay an interrupted network batch as if no requests happened.
            if state.get("poll_pending"):
                checkpoint = await active.append(
                    session_id,
                    f"poll:{state['poll_attempts']}:interrupted",
                    {"action": "failure", "reason": "interrupted_collection"},
                    stamp,
                )
                state = cast(dict[str, Any], checkpoint["state"])
            if state.get("poll_attempts", 0) >= config.max_cycles:
                break
            number = int(state.get("poll_attempts", 0)) + 1
            checkpoint = await active.append(
                session_id, f"poll:{number}", {"action": "poll"}, stamp
            )
            if cast(dict[str, object], checkpoint["state"])["status"] == "STOPPED":
                break
            try:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    payload: dict[str, object] = {
                        "action": "failure",
                        "reason": "invocation_deadline",
                    }
                else:
                    async with asyncio.timeout(min(15, remaining)):
                        payload = await collector(config)
            except (ValueError, OSError, TimeoutError, TransportError):
                # Provider bodies, arbitrary URLs and credentials are never diagnostics.
                payload = {"action": "failure", "reason": "collection_unavailable"}
            result_at = clock()
            if payload.get("action") == "tick":
                from ai_trading_lab.strategies.models import StrategyFrame

                result_at = StrategyFrame.model_validate(payload.get("frame")).at_utc
            checkpoint = await active.append(
                session_id, f"poll:{number}:result", payload, result_at
            )
            await queue_checkpoint(journal.engine, checkpoint, clock())
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            if _ + 1 < cycles:
                await sleep(min(float(config.poll_interval_seconds), remaining))
        await queue_checkpoint(journal.engine, checkpoint, clock())
        return checkpoint
