"""Worker bounds with synthetic sources: hung collectors, wiring limits, concurrent stop."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.runtime import worker as worker_module
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.runtime.worker import run_worker
from tests.unit.test_runtime_source import observed_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def session(engine: AsyncEngine, now: datetime) -> tuple[Journal, RuntimeConfig]:
    original, _ = observed_config()
    spec = original.model_copy(
        update={
            "started_at_utc": now - timedelta(minutes=1),
            "deadline_at_utc": now + timedelta(days=1),
            "max_cycles": 5,
        }
    )
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc)
    return journal, spec


async def test_hung_collector_is_cut_off_by_the_invocation_bound(engine: AsyncEngine) -> None:
    journal, spec = await session(engine, datetime.now(UTC))

    async def hangs(_: RuntimeConfig) -> dict[str, object]:
        await asyncio.sleep(60)
        raise AssertionError("collector must be cancelled by the worker bound")

    started = time.monotonic()
    result: Any = await run_worker(journal, spec.session_id, cycles=1, seconds=1, collector=hangs)
    assert time.monotonic() - started < 10
    state = result["state"]
    assert state["poll_attempts"] == 1 and state["poll_pending"] is False
    assert state["suspension_reason"] == "collection_failure"
    assert state["portfolio"]["cash"] == str(spec.initial_cash)


async def test_production_collector_is_bounded_and_single_attempt() -> None:
    """The unmocked collector path was never executed; pin its network limits offline."""
    seen: dict[str, Any] = {}

    class FakeTransport:
        def __init__(self, *, limits: Any) -> None:
            seen["limits"] = limits

        async def __aenter__(self) -> "FakeTransport":
            return self

        async def __aexit__(self, *_: object) -> None:
            seen["closed"] = True

    async def collected(config: RuntimeConfig, transport: object) -> dict[str, object]:
        seen["transport"] = transport
        return {"action": "failure", "reason": "synthetic"}

    spec, _ = observed_config()
    with (
        patch.object(worker_module, "HttpTransport", FakeTransport),
        patch.object(worker_module, "collect_frame", collected),
    ):
        assert await worker_module.observed_collector(spec) == {
            "action": "failure",
            "reason": "synthetic",
        }
    limits = seen["limits"]
    assert isinstance(seen["transport"], FakeTransport) and seen["closed"] is True
    assert limits.max_requests == 2 and limits.max_attempts == 1 and limits.concurrency == 1
    assert limits.timeout_seconds <= 5 and limits.total_seconds <= 10
    assert limits.max_response_bytes <= 100_000


async def test_stop_during_an_in_flight_poll_ends_cleanly(engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    journal, spec = await session(engine, now)

    async def stopped_midflight(_: RuntimeConfig) -> dict[str, object]:
        await Journal(engine, reducer).append(
            spec.session_id, "operator-stop", {"action": "stop"}, datetime.now(UTC)
        )
        return {"action": "failure", "reason": "collection_unavailable"}

    result: Any = await run_worker(
        journal, spec.session_id, cycles=3, seconds=30, collector=stopped_midflight
    )
    assert result["state"]["status"] == "STOPPED"
    assert result["state"]["poll_pending"] is False
    assert result["state"]["poll_attempts"] == 1


async def test_f04_worker_recovers_after_one_failure_and_announces_it(
    engine: AsyncEngine,
) -> None:
    """DEC-039 through the real worker: failure, then three fresh ticks clear entries."""
    from unittest.mock import AsyncMock

    from tests.integration.test_runtime_benchmark import observed_payload

    original, entry = observed_config()
    start = entry.book_capture.received_at_utc
    spec = original.model_copy(
        update={
            "started_at_utc": start,
            "deadline_at_utc": start + timedelta(days=1),
            "max_cycles": 8,
        }
    )
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), start)
    now = [start]
    calls = 0

    async def collector(_: RuntimeConfig) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("synthetic outage")
        at, payload = observed_payload(calls, start)
        now[0] = at
        return payload

    async def no_sleep(_: float) -> None:
        return None

    store = AsyncMock()
    with patch("ai_trading_lab.runtime.notifications.NotificationStore", return_value=store):
        result: Any = await run_worker(
            journal,
            spec.session_id,
            cycles=3,
            seconds=60,
            collector=collector,
            clock=lambda: now[0],
            monotonic=lambda: 0,
            sleep=no_sleep,
        )
        assert result["state"]["status"] == "DEGRADED"  # one failure + two ticks: not yet
        result = await run_worker(
            journal,
            spec.session_id,
            cycles=1,
            seconds=60,
            collector=collector,
            clock=lambda: now[0],
            monotonic=lambda: 0,
            sleep=no_sleep,
        )
    state = result["state"]
    assert state["status"] == "RUNNING" and state["auto_clears"] == 1
    assert state["poll_attempts"] == 4 and state["cycles"] == 3
    kinds = [call.args[0].kind for call in store.enqueue.await_args_list]
    assert "recovery" in kinds
