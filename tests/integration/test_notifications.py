"""Real isolated PostgreSQL claims/restart recovery with injected Telegram transport."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.store import NotificationStore, notifications
from ai_trading_lab.notifications.telegram import TelegramAdapter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def event() -> Notification:
    return Notification(
        event_key=str(uuid4()),
        kind="safety_halt",
        occurred_at_utc=AT,
        text="Fixture exposure halted",
    )


def adapter(
    handler: Callable[[httpx2.Request], httpx2.Response] | None = None, enabled: bool = True
) -> TelegramAdapter:
    def success(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"ok": True, "result": {"message_id": 7, "chat": {"id": 1}}}
        )

    return TelegramAdapter(
        SecretStr("123:fixture_abcdefghijklmnopqrst"),
        SecretStr("1"),
        enabled=enabled,
        transport=httpx2.MockTransport(success if handler is None else handler),
    )


async def test_duplicate_enqueue_conflict_and_concurrent_dispatch(engine: AsyncEngine) -> None:
    store, item = NotificationStore(engine), event()
    a, b = await asyncio.gather(*(store.enqueue(item, AT) for _ in range(2)))
    assert a == b and a["state"] == "pending"
    with pytest.raises(ValueError, match="conflict"):
        await store.enqueue(item.model_copy(update={"text": "changed"}), AT)
    calls = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(
            200, json={"ok": True, "result": {"message_id": 7, "chat": {"id": 1}}}
        )

    client = adapter(handle)
    await asyncio.gather(*(store.dispatch(item.event_key, client, AT) for _ in range(2)))
    assert len(calls) == 1
    row = await NotificationStore(engine).dispatch(item.event_key, client, AT)
    assert row["state"] == "delivered" and row["message_id"] == 7 and len(calls) == 1
    assert row["destination_hash"] == client.destination_hash


async def test_interruption_persisted_before_network_never_retried(engine: AsyncEngine) -> None:
    store, item = NotificationStore(engine), event()
    await store.enqueue(item, AT)

    def crash(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("synthetic worker death")

    with pytest.raises(RuntimeError):
        await store.dispatch(item.event_key, adapter(crash), AT)
    assert (await store.get(item.event_key))["state"] == "delivering"
    assert (await NotificationStore(engine).dispatch(item.event_key, adapter(), AT))[
        "state"
    ] == "delivering"
    assert (await store.recover_unknown(item.event_key, AT))["state"] == "unknown"
    assert (await store.dispatch(item.event_key, adapter(), AT))["state"] == "unknown"


async def test_clock_disabled_missing_and_integrity(engine: AsyncEngine) -> None:
    store, item = NotificationStore(engine), event()
    with pytest.raises(ValueError, match="predates"):
        await store.enqueue(item, AT - timedelta(seconds=1))
    await store.enqueue(item, AT)
    assert (await store.dispatch(item.event_key, adapter(enabled=False), AT))["state"] == "pending"
    with pytest.raises(ValueError, match="clock"):
        await store.dispatch(item.event_key, adapter(), AT - timedelta(seconds=1))
    with pytest.raises(ValueError, match="not found"):
        await store.get(str(uuid4()))
    async with engine.begin() as connection:
        await connection.execute(
            update(notifications)
            .where(notifications.c.event_key == item.event_key)
            .values(event_hash="0" * 64)
        )
    with pytest.raises(ValueError, match="integrity"):
        await store.dispatch(item.event_key, adapter(), AT)


@pytest.mark.parametrize("status", [429, 500])
async def test_failed_delivery_is_terminal(engine: AsyncEngine, status: int) -> None:
    store, item = NotificationStore(engine), event()
    await store.enqueue(item, AT)
    calls = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(status, json={"ok": False})

    client = adapter(handle)
    first = await store.dispatch(item.event_key, client, AT)
    second = await store.dispatch(item.event_key, client, AT)
    assert first == second and len(calls) == 1
    assert first["state"] == ("rejected" if status == 429 else "unknown")


@pytest.mark.parametrize(
    "change",
    [
        {"state": "made_up"},
        {"claim_id": "unproved"},
        {"message_id": 1},
        {"destination_hash": "0" * 64},
    ],
)
async def test_corrupt_delivery_projection_refused(
    engine: AsyncEngine, change: dict[str, object]
) -> None:
    store, item = NotificationStore(engine), event()
    await store.enqueue(item, AT)
    async with engine.begin() as connection:
        await connection.execute(
            update(notifications)
            .where(notifications.c.event_key == item.event_key)
            .values(**change)
        )
    with pytest.raises(ValueError, match="invalid"):
        await store.get(item.event_key)


async def test_missing_and_recovery_clock_boundaries(engine: AsyncEngine) -> None:
    store, item = NotificationStore(engine), event()
    with pytest.raises(ValueError, match="not found"):
        await store.dispatch(item.event_key, adapter(), AT)
    with pytest.raises(ValueError, match="not found"):
        await store.recover_unknown(item.event_key, AT)
    await store.enqueue(item, AT)
    assert (await store.recover_unknown(item.event_key, AT))["state"] == "pending"
    async with engine.begin() as connection:
        await connection.execute(
            update(notifications)
            .where(notifications.c.event_key == item.event_key)
            .values(
                state="delivering",
                claim_id=str(uuid4()),
                destination_hash="0" * 64,
                attempted_at_utc=AT,
            )
        )
    with pytest.raises(ValueError, match="clock"):
        await store.recover_unknown(item.event_key, AT - timedelta(seconds=1))
    assert (await store.recover_unknown(item.event_key, AT))["state"] == "unknown"
