"""Real durable worker/engine integration with virtual clocks and no public requests."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.runtime.worker import run_worker
from tests.backtest_helpers import research
from tests.unit.test_runtime_engine import config, tick
from tests.unit.test_runtime_source import observed_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def test_persistent_engine_restart_does_not_fund_again(engine: AsyncEngine) -> None:
    raw = config()
    spec = RuntimeConfig.model_validate(raw)
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, raw, spec.started_at_utc)
    frame = research().frames[0]
    payload: Any = tick(frame, 1)["payload"]
    first: Any = await journal.append(spec.session_id, "first", payload, frame.at_utc)
    assert first["state"]["portfolio"]["positions"]
    restored = await Journal(engine, reducer).get(spec.session_id)
    assert restored == first
    assert await journal.append(spec.session_id, "first", payload, frame.at_utc) == first
    later = frame.at_utc + timedelta(seconds=1)
    second: Any = await journal.append(spec.session_id, "pause", {"action": "pause"}, later)
    assert second["state"]["portfolio"]["cash"] == first["state"]["portfolio"]["cash"]
    assert second["state"]["portfolio"]["fees"] == first["state"]["portfolio"]["fees"]
    assert second["state"]["portfolio"]["initial_cash"] == str(spec.initial_cash)


async def test_worker_records_attempts_and_recovery_before_any_network_retry(
    engine: AsyncEngine,
) -> None:
    original, entry = observed_config()
    spec = original.model_copy(
        update={
            "started_at_utc": entry.book_capture.received_at_utc,
            "deadline_at_utc": entry.book_capture.received_at_utc + timedelta(days=1),
            "max_cycles": 2,
        }
    )
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc)
    async with journal.worker(spec.session_id) as active:
        await active.append(spec.session_id, "poll:1", {"action": "poll"}, spec.started_at_utc)
    calls = 0

    async def failure(_: RuntimeConfig) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise TimeoutError("private provider URL")

    async def sleep(_: float) -> None:
        raise AssertionError("Last cycle should not sleep")

    result = await run_worker(
        journal,
        spec.session_id,
        cycles=1,
        seconds=10,
        collector=failure,
        clock=lambda: spec.started_at_utc + timedelta(seconds=1),
        monotonic=lambda: 0,
        sleep=sleep,
    )
    assert calls == 1
    state: Any = result["state"]
    assert state["poll_attempts"] == 2 and state["poll_pending"] is False
    assert state["status"] == "STOPPED"
    assert state["portfolio"]["cash"] == str(spec.initial_cash)
    assert "private" not in str(result)
    await run_worker(
        journal,
        spec.session_id,
        cycles=1,
        seconds=10,
        collector=failure,
        clock=lambda: spec.started_at_utc + timedelta(seconds=2),
        monotonic=lambda: 0,
        sleep=sleep,
    )
    assert calls == 1


async def test_fixture_worker_refuses_before_collector(engine: AsyncEngine) -> None:
    spec = RuntimeConfig.model_validate(config())
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc)

    async def forbidden(_: RuntimeConfig) -> dict[str, object]:
        raise AssertionError("No transport for fixture worker")

    with pytest.raises(ValueError, match="fixture"):
        await run_worker(journal, spec.session_id, cycles=1, seconds=1, collector=forbidden)


@pytest.mark.parametrize("cycles,seconds", [(0, 1), (10001, 1), (1, 0), (1, 345601), (True, 1)])
async def test_worker_bounds_before_database(
    cycles: int, seconds: int, engine: AsyncEngine
) -> None:
    async def forbidden(_: RuntimeConfig) -> dict[str, object]:
        raise AssertionError("No source for invalid bounds")

    with pytest.raises(ValueError):
        await run_worker(
            Journal(engine, reducer),
            UUID(int=1),
            cycles=cycles,
            seconds=seconds,
            collector=forbidden,
        )


async def test_notification_outage_does_not_stop_qualified_monitoring(
    engine: AsyncEngine, capsys: Any
) -> None:
    from unittest.mock import AsyncMock, patch

    from sqlalchemy.exc import SQLAlchemyError

    from ai_trading_lab.runtime.source import collect_frame

    original, entry = observed_config()
    at = entry.book_capture.received_at_utc
    spec = original.model_copy(
        update={"started_at_utc": at, "deadline_at_utc": at + timedelta(days=1), "max_cycles": 2}
    )
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), at)
    await journal.append(spec.session_id, "pause", {"action": "pause"}, at)
    calls = 0
    transport = AsyncMock()
    transport.request.side_effect = [entry.metadata_capture, entry.book_capture] * 2

    async def collector(cfg: RuntimeConfig) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return await collect_frame(cfg, transport, clock=lambda: at)

    outbox = AsyncMock()
    outbox.enqueue.side_effect = SQLAlchemyError("private recipient credentials")
    with patch("ai_trading_lab.runtime.notifications.NotificationStore", return_value=outbox):
        result: Any = await run_worker(
            journal,
            spec.session_id,
            cycles=1,
            seconds=10,
            collector=collector,
            clock=lambda: at,
            monotonic=lambda: 0,
        )
        assert result["state"]["cycles"] == 1 and calls == 1
        restarted: Any = await run_worker(
            journal,
            spec.session_id,
            cycles=1,
            seconds=10,
            collector=collector,
            clock=lambda: at,
            monotonic=lambda: 0,
        )
    assert restarted["state"]["cycles"] == 2 and calls == 2
    assert transport.request.await_count == 4
    assert restarted["state"]["status"] == "STOPPED"
    assert restarted["state"]["portfolio"]["cash"] == str(spec.initial_cash)
    assert "private" not in capsys.readouterr().err


async def test_observed_poll_result_is_owned_by_worker_connection(engine: AsyncEngine) -> None:
    """Synthetic protocol acceptance only; no observed source claims or network."""
    from ai_trading_lab.runtime.evidence import observed_quote

    spec, entry = observed_config()
    at = entry.book_capture.received_at_utc
    spec = spec.model_copy(update={"started_at_utc": at})
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), at)
    frame = (
        research()
        .frames[0]
        .model_copy(
            update={
                "quote": observed_quote(entry),
                "at_utc": at,
                "forecast": None,
                "resolution_terms_hash": entry.resolution_terms_hash,
            }
        )
    )
    payload = tick(frame, 1, entry)["payload"]
    with pytest.raises(ValueError, match="worker lease"):
        await journal.append(spec.session_id, "poll:1", {"action": "poll"}, at)
    async with journal.worker(spec.session_id) as active:
        with pytest.raises(ValueError, match="poll_result_required"):
            await active.append(spec.session_id, "poll:1:result", payload, at)
        await active.append(spec.session_id, "poll:1", {"action": "poll"}, at)
        with pytest.raises(ValueError, match="worker lease"):
            await journal.append(spec.session_id, "poll:1:result", payload, at)
        with pytest.raises(ValueError, match="poll_result_required"):
            await active.append(spec.session_id, "wrong", payload, at)
        accepted: Any = await active.append(spec.session_id, "poll:1:result", payload, at)
        assert accepted["state"]["cycles"] == 1
        assert accepted["state"]["poll_pending"] is False
        assert await active.append(spec.session_id, "poll:1:result", payload, at) == accepted
    with pytest.raises(ValueError, match="worker lease"):
        await journal.append(spec.session_id, "poll:1:result", payload, at)
    stopped: Any = await journal.append(spec.session_id, "operator-stop", {"action": "stop"}, at)
    assert stopped["state"]["status"] == "STOPPED"
