"""Real-reducer PostgreSQL admission, compact recovery and warmed-cache tamper checks."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.runtime.capacity import MAX_JOURNAL_BYTES
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.journal import Journal, events
from ai_trading_lab.runtime.models import RuntimeConfig
from tests.unit.test_experiment_runner import tape_with_exit
from tests.unit.test_runtime_engine import config, tick

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def funded(engine: AsyncEngine) -> tuple[Journal, RuntimeConfig, dict[str, Any]]:
    spec = RuntimeConfig.model_validate(config())
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc)
    result: dict[str, Any] = {}
    for index, frame in enumerate(tape_with_exit(), 1):
        result = await journal.append(
            spec.session_id, f"tick:{index}", tick(frame, index)["payload"], frame.at_utc
        )
    return journal, spec, result


async def test_compact_fills_recover_exactly_and_hot_append_never_replays(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, spec, final = await funded(engine)
    state = final["state"]
    assert state["history_count"] == 3
    assert "history" not in state and "native_events" not in state
    assert "source_entry" not in json.dumps(final)
    assert await Journal(engine, reducer).get(spec.session_id) == final

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Hot append reconstructed full runtime")

    monkeypatch.setattr("ai_trading_lab.runtime.journal.RuntimeReducer", forbidden)
    result = await journal.append(
        spec.session_id, "pause", {"action": "pause"}, spec.started_at_utc + timedelta(seconds=3)
    )
    assert cast(Any, result["state"])["history_count"] == 3


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
async def test_warmed_cache_detects_old_evidence_mutation(
    engine: AsyncEngine, mutation: str
) -> None:
    from sqlalchemy import text

    journal, spec, final = await funded(engine)
    assert await journal.get(spec.session_id) == final
    async with engine.begin() as connection:
        condition = (events.c.session_id == spec.session_id) & (events.c.sequence == 1)
        if mutation == "update":
            await connection.execute(update(events).where(condition).values(chain_hash="0" * 64))
        elif mutation == "delete":
            await connection.execute(delete(events).where(condition))
        else:
            await connection.execute(text("TRUNCATE runtime_events"))
    with pytest.raises(ValueError, match="integrity"):
        await journal.get(spec.session_id)
    with pytest.raises(ValueError, match="integrity"):
        await journal.append(
            spec.session_id,
            "pause",
            {"action": "pause"},
            spec.started_at_utc + timedelta(seconds=3),
        )


async def test_declared_oversized_result_stops_with_reserved_capacity(engine: AsyncEngine) -> None:
    raw = config()
    raw.update(max_cycles=1, max_journal_bytes=128 * 1024, max_event_bytes=1024)
    spec = RuntimeConfig.model_validate(raw)
    journal = Journal(engine, reducer)
    initial = await journal.create(spec.session_id, raw, spec.started_at_utc)
    frame = tape_with_exit()[0]
    result: Any = await journal.append(
        spec.session_id, "tick:1", tick(frame, 1)["payload"], frame.at_utc
    )
    assert result["state"]["status"] == "STOPPED"
    assert result["state"]["suspension_reason"] == "capacity"
    assert result["state"]["cycles"] == 0
    assert result["journal_bytes"] <= spec.max_journal_bytes
    assert cast(Any, result["state"])["portfolio"] == cast(Any, initial["state"])["portfolio"]
    async with engine.connect() as connection:
        envelope = await connection.scalar(
            select(events.c.event).where(events.c.session_id == spec.session_id)
        )
    payload: Any = cast(Any, envelope)["payload"]
    assert payload["refused_event_bytes"] > 1024
    assert len(payload["refused_event_hash"]) == 64
    assert await Journal(engine, reducer).get(spec.session_id) == result
    assert MAX_JOURNAL_BYTES == 512 * 1024 * 1024


async def test_f14_replay_yields_to_the_event_loop_between_events(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import time

    from ai_trading_lab.runtime.engine import RuntimeReducer

    _, spec, _ = await funded(engine)
    original = RuntimeReducer.apply

    def slow(self: RuntimeReducer, event: dict[str, object]) -> dict[str, object]:
        time.sleep(0.05)  # CPU-bound replay step
        return original(self, event)

    monkeypatch.setattr(RuntimeReducer, "apply", slow)
    stalls: list[float] = []
    stop = False

    async def heartbeat() -> None:
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            stalls.append(now - last)
            last = now

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.02)
    await Journal(engine, reducer).get(spec.session_id)  # fresh instance: full replay
    stop = True
    await task
    frames = len(tape_with_exit())
    assert frames >= 3
    # Each replay step blocks 50 ms. Yielding between events bounds any stall to about one
    # step; a loop held for the whole replay stalls for roughly frames x 50 ms.
    assert max(stalls) < 0.05 * (frames - 1)


async def test_f12_runtime_report_is_labelled_mark_only(engine: AsyncEngine) -> None:
    from ai_trading_lab.reporting.service import ReportingService

    _, spec, _ = await funded(engine)
    report = await ReportingService(engine).runtime_detail(spec.session_id)
    assert report["economic_basis"] == "mark_only_no_realization"
    assert report["final_pnl_claim"] is False


async def test_control_retry_with_a_new_clock_replays_by_key_and_payload(
    engine: AsyncEngine,
) -> None:
    journal, spec, final = await funded(engine)
    at = spec.started_at_utc + timedelta(hours=1)
    first = await journal.append(spec.session_id, "op-pause", {"action": "pause"}, at)
    retried = await journal.append(
        spec.session_id, "op-pause", {"action": "pause"}, at + timedelta(seconds=40)
    )
    assert retried == first
    with pytest.raises(ValueError, match="key conflict"):
        await journal.append(
            spec.session_id, "op-pause", {"action": "resume"}, at + timedelta(seconds=40)
        )


async def test_stale_cached_continuation_is_never_extended(engine: AsyncEngine) -> None:
    from ai_trading_lab.runtime.engine import RuntimeReducer

    journal, spec, final = await funded(engine)
    marker = next(iter(journal._cache.values()))[:2]
    stale = RuntimeReducer(spec.model_dump(mode="json"))  # initial state, before any tick
    journal._cache[spec.session_id] = (*marker, stale)
    at = spec.started_at_utc + timedelta(hours=1)
    await journal.append(spec.session_id, "after-stale", {"action": "pause"}, at)
    # A fresh journal replays the whole tape and compares every stored state.
    replayed: Any = await Journal(engine, reducer).get(spec.session_id)
    assert replayed["state"]["status"] == "PAUSED"
    assert replayed["state"]["history_count"] == final["state"]["history_count"]
