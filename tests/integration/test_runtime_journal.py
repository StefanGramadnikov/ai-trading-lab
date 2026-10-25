"""Isolated PostgreSQL runtime history, restart, conflict and ownership cases."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.runtime.journal import Journal, events, sessions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
AT = datetime(2026, 10, 20, tzinfo=UTC)


def reduce_total(
    config: dict[str, object], tape: tuple[dict[str, object], ...]
) -> dict[str, object]:
    total = int(str(config.get("initial", 0)))
    for item in tape:
        payload = item["payload"]
        assert isinstance(payload, dict)
        total += int(str(payload["amount"]))
    return {"total": total, "count": len(tape)}


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def populated(engine: AsyncEngine) -> tuple[Journal, UUID]:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    await journal.create(session_id, {"initial": 4}, AT)
    await journal.append(session_id, "one", {"amount": 3}, AT)
    await journal.append(session_id, "two", {"amount": 2}, AT + timedelta(seconds=1))
    return journal, session_id


async def test_create_restart_concurrent_append_and_original_snapshot(engine: AsyncEngine) -> None:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    zero = await journal.create(session_id, {"initial": 4}, AT)
    assert zero["sequence"] == 0 and zero["state"] == {"total": 4, "count": 0}
    assert await journal.create(session_id, {"initial": 4}, AT) == zero
    first, retry = await asyncio.gather(
        journal.append(session_id, "one", {"amount": 3}, AT),
        journal.append(session_id, "one", {"amount": 3}, AT),
    )
    assert first == retry
    a, b = await asyncio.gather(
        journal.append(session_id, "two", {"amount": 5}, AT),
        journal.append(session_id, "three", {"amount": 7}, AT),
    )
    assert {a["sequence"], b["sequence"]} == {2, 3}
    recovered = await Journal(engine, reduce_total).get(session_id)
    assert recovered["state"] == {"total": 19, "count": 3}
    assert await journal.append(session_id, "one", {"amount": 3}, AT) == first
    assert await journal.config(session_id) == {"initial": 4}
    assert await journal.create(session_id, {"initial": 4}, AT) == recovered
    with pytest.raises(ValueError, match="configuration conflict"):
        await journal.create(session_id, {"initial": 5}, AT)
    with pytest.raises(ValueError, match="configuration conflict"):
        await journal.create(session_id, {"initial": 4}, AT + timedelta(seconds=1))
    conflicts: list[tuple[dict[str, object], datetime]] = [
        ({"amount": 4}, AT),
        ({"amount": 3}, AT + timedelta(seconds=1)),
    ]
    for payload, at in conflicts:
        with pytest.raises(ValueError, match="event key conflict"):
            await journal.append(session_id, "one", payload, at)
    assert await journal.get(session_id) == recovered


async def test_clock_key_json_identity_missing_and_capacity(engine: AsyncEngine) -> None:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    with pytest.raises(ValueError, match="timezone aware"):
        await journal.create(session_id, {}, AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="native UUID"):
        await journal.get(str(session_id))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not found"):
        await journal.get(session_id)
    configs: list[dict[str, object]] = [
        {"max_cycles": True},
        {"max_cycles": 10001},
        {"max_journal_bytes": -1},
    ]
    for config in configs:
        with pytest.raises(ValueError, match="invalid runtime"):
            await journal.create(session_id, config, AT)
    with pytest.raises(ValueError, match="capacity"):
        await journal.create(session_id, {"max_journal_bytes": 1}, AT)
    await journal.create(session_id, {"max_journal_bytes": 1500}, AT)
    before = await journal.get(session_id)
    with pytest.raises(ValueError, match="backwards"):
        await journal.append(session_id, "past", {"amount": 1}, AT - timedelta(seconds=1))
    for key in ["", "x" * 201]:
        with pytest.raises(ValueError, match="event key"):
            await journal.append(session_id, key, {"amount": 1}, AT)
    with pytest.raises(ValueError, match="JSON compliant"):
        await journal.append(session_id, "nan", {"amount": float("nan")}, AT)
    with pytest.raises(ValueError, match="capacity"):
        await journal.append(session_id, "large", {"amount": 1, "padding": "x" * 1500}, AT)
    assert await journal.get(session_id) == before
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count()).select_from(events).where(events.c.session_id == session_id)
            )
            == 0
        )


async def test_event_count_capacity_and_retry_after_capacity(engine: AsyncEngine) -> None:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    await journal.create(session_id, {"max_cycles": 1}, AT)
    first = await journal.append(session_id, "0", {"amount": 1}, AT)
    for index in range(1, 34):
        await journal.append(session_id, str(index), {"amount": 1}, AT)
    with pytest.raises(ValueError, match="event capacity"):
        await journal.append(session_id, "34", {"amount": 1}, AT)
    assert await journal.append(session_id, "0", {"amount": 1}, AT) == first
    assert (await journal.get(session_id))["sequence"] == 34


@pytest.mark.parametrize(
    ("table", "changes"),
    [
        ("session", {"config": {"initial": 9}}),
        ("session", {"engine_hash": "0" * 64}),
        ("session", {"checkpoint_hash": "0" * 64}),
        ("session", {"chain_hash": "0" * 64}),
        ("session", {"created_at": AT.replace(tzinfo=None).isoformat()}),
        ("event", {"event_key": "renamed"}),
        ("event", {"sequence": 10}),
        ("event", {"event": {"key": "one", "at": AT.isoformat(), "payload": {"amount": 8}}}),
        ("event", {"checkpoint_hash": "0" * 64}),
        ("event", {"chain_hash": "0" * 64}),
    ],
)
async def test_corrupt_rows_fail_closed_without_repair(
    engine: AsyncEngine,
    table: str,
    changes: dict[str, Any],
) -> None:
    journal, session_id = await populated(engine)
    target = sessions if table == "session" else events
    condition = target.c.session_id == session_id
    if table == "event":
        condition &= events.c.sequence == 1
    async with engine.begin() as connection:
        await connection.execute(update(target).where(condition).values(**changes))
    with pytest.raises(ValueError, match="integrity"):
        await journal.get(session_id)
    with pytest.raises(ValueError, match="integrity"):
        await journal.append(session_id, "new", {"amount": 1}, AT + timedelta(seconds=2))


@pytest.mark.parametrize("sequence", [1, 2])
async def test_deleted_event_never_restarts_from_new_portfolio(
    engine: AsyncEngine, sequence: int
) -> None:
    journal, session_id = await populated(engine)
    async with engine.begin() as connection:
        await connection.execute(
            delete(events).where(events.c.session_id == session_id, events.c.sequence == sequence)
        )
    with pytest.raises(ValueError, match="integrity"):
        await journal.get(session_id)


async def test_coherently_altered_final_state_rejected_by_reducer(engine: AsyncEngine) -> None:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    zero = await journal.create(session_id, {}, AT)
    snapshot = await journal.append(session_id, "one", {"amount": 1}, AT)
    snapshot["state"] = {"total": 9, "count": 1}
    chain = content_hash(
        [
            content_hash(zero),
            {"key": "one", "at": AT.isoformat(), "payload": {"amount": 1}},
            content_hash(snapshot),
        ]
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(events)
            .where(events.c.session_id == session_id)
            .values(checkpoint=snapshot, checkpoint_hash=content_hash(snapshot), chain_hash=chain)
        )
        await connection.execute(
            update(sessions)
            .where(sessions.c.session_id == session_id)
            .values(checkpoint=snapshot, checkpoint_hash=content_hash(snapshot), chain_hash=chain)
        )
    with pytest.raises(ValueError, match="integrity"):
        await journal.get(session_id)


async def test_reducer_failure_rolls_back_and_code_mismatch_blocks(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, session_id = await populated(engine)
    before = await journal.get(session_id)
    with pytest.raises(ValueError, match="invalid literal"):
        await journal.append(session_id, "bad", {"amount": "bad"}, AT + timedelta(seconds=2))
    assert await journal.get(session_id) == before
    monkeypatch.setattr("ai_trading_lab.runtime.journal.runtime_fingerprint", lambda: "0" * 64)
    with pytest.raises(ValueError, match="code integrity"):
        await journal.get(session_id)


async def test_worker_rejects_second_owner_and_releases_after_exception(
    engine: AsyncEngine,
) -> None:
    journal, session_id = Journal(engine, reduce_total), uuid4()
    async with journal.worker(session_id):
        with pytest.raises(ValueError, match="already owned"):
            async with Journal(engine, reduce_total).worker(session_id):
                pytest.fail("second worker acquired session")
    with pytest.raises(RuntimeError, match="fixture worker death"):
        async with journal.worker(session_id):
            raise RuntimeError("fixture worker death")
    async with journal.worker(session_id):
        pass


async def test_cancelled_worker_releases_session_lock(engine: AsyncEngine) -> None:
    journal, session_id, ready = Journal(engine, reduce_total), uuid4(), asyncio.Event()

    async def work() -> None:
        async with journal.worker(session_id):
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with journal.worker(session_id):
        pass


async def test_recovery_reduces_full_history_once_not_each_prefix(engine: AsyncEngine) -> None:
    journal, session_id = await populated(engine)
    calls: list[int] = []

    def counted(
        config: dict[str, object], tape: tuple[dict[str, object], ...]
    ) -> dict[str, object]:
        calls.append(len(tape))
        return reduce_total(config, tape)

    await Journal(engine, counted).get(session_id)
    assert calls == [0, 2]


async def test_bound_worker_uses_owned_connection_and_expires_on_exit(engine: AsyncEngine) -> None:
    journal, session_id = await populated(engine)
    async with journal.worker(session_id) as active:
        assert (await active.get(session_id))["sequence"] == 2
        assert await active.config(session_id) == {"initial": 4}
        result = await active.append(session_id, "owned", {"amount": 1}, AT + timedelta(seconds=2))
        assert result["sequence"] == 3
        with pytest.raises(ValueError, match="lease is inactive"):
            await active.get(uuid4())
        with pytest.raises(ValueError, match="already bound"):
            async with active.worker(session_id):
                pytest.fail("nested lease unexpectedly acquired")
    with pytest.raises(ValueError, match="lease is inactive"):
        await active.append(session_id, "expired", {"amount": 1}, AT + timedelta(seconds=3))
    async with journal.worker(session_id) as restarted:
        assert (await restarted.get(session_id))["sequence"] == 3


async def test_lost_lock_connection_cannot_write_using_new_pooled_connection(
    engine: AsyncEngine,
) -> None:
    journal, session_id = await populated(engine)
    async with journal.worker(session_id) as active:
        connection = active._connection
        assert connection is not None
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        await connection.commit()
        async with engine.begin() as control:
            assert await control.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with pytest.raises(DBAPIError):
            await active.append(session_id, "lost", {"amount": 10}, AT + timedelta(seconds=2))
        with pytest.raises(ValueError, match="lease is inactive"):
            await active.get(session_id)
        async with journal.worker(session_id) as new_owner:
            assert (await new_owner.get(session_id))["sequence"] == 2
            await new_owner.append(session_id, "newowner", {"amount": 2}, AT + timedelta(seconds=2))
        with pytest.raises(ValueError, match="lease is inactive"):
            await active.append(session_id, "stale", {"amount": 10}, AT + timedelta(seconds=3))
    assert (await journal.get(session_id))["state"] == {"count": 3, "total": 11}


async def test_lost_acquisition_response_discards_lock_connection(engine: AsyncEngine) -> None:
    from sqlalchemy import event

    journal, session_id = Journal(engine, reduce_total), uuid4()
    failed = False

    def lose_response(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        nonlocal failed
        if not failed and "pg_try_advisory_lock" in statement:
            failed = True
            raise RuntimeError("fixture response lost after lock acquired")

    event.listen(engine.sync_engine, "after_cursor_execute", lose_response)
    try:
        with pytest.raises(RuntimeError, match="response lost"):
            async with journal.worker(session_id):
                pytest.fail("lost acquisition response accepted")
        async with journal.worker(session_id):
            pass
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", lose_response)
