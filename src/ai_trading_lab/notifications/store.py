"""Durable single-attempt outbox; an interrupted claim is never resent."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, String, Table, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.notifications.models import DeliveryResult, Notification, utc
from ai_trading_lab.notifications.telegram import TelegramAdapter


def bounded[**P, R](operation: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> R:
        # Includes waiting for a pooled connection/row lock and driver cleanup.
        async with asyncio.timeout(10):
            return await operation(*args, **kwargs)

    return run


notifications = Table(
    "notification_outbox",
    metadata,
    Column("event_key", String(200), primary_key=True),
    Column("event_hash", String(64), nullable=False),
    Column("event", JSONB, nullable=False),
    Column("received_at_utc", DateTime(timezone=True), nullable=False),
    Column("state", String(20), nullable=False),
    Column("claim_id", String(36)),
    Column("destination_hash", String(64)),
    Column("attempted_at_utc", DateTime(timezone=True)),
    Column("finished_at_utc", DateTime(timezone=True)),
    Column("reason", String(40)),
    Column("message_id", Integer),
)


def _validate(row: Any) -> dict[str, Any]:
    event = Notification.model_validate(row["event"])
    if (
        event.event_key != row["event_key"]
        or content_hash(event.model_dump(mode="json")) != row["event_hash"]
    ):
        raise ValueError("Notification integrity conflict")
    state = row["state"]
    if state not in {"pending", "delivering", "delivered", "rejected", "unknown"}:
        raise ValueError("Notification state invalid")
    if (state == "pending") != (row["claim_id"] is None):
        raise ValueError("Notification claim invalid")
    if (state == "delivered") != (row["message_id"] is not None):
        raise ValueError("Notification confirmation invalid")
    if (state == "pending") != (row["destination_hash"] is None):
        raise ValueError("Notification destination invalid")
    return dict(row)


class NotificationStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @bounded
    async def enqueue(self, event: Notification, received_at_utc: datetime) -> dict[str, Any]:
        received = utc(received_at_utc)
        if received < event.occurred_at_utc:
            raise ValueError("Notification receipt predates event")
        payload = event.model_dump(mode="json")
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(notifications)
                .values(
                    event_key=event.event_key,
                    event_hash=content_hash(payload),
                    event=payload,
                    received_at_utc=received,
                    state="pending",
                )
                .on_conflict_do_nothing(index_elements=[notifications.c.event_key])
            )
            row = (
                (
                    await connection.execute(
                        select(notifications).where(notifications.c.event_key == event.event_key)
                    )
                )
                .mappings()
                .one()
            )
            if row["event_hash"] != content_hash(payload):
                raise ValueError("Notification key conflict")
            return _validate(row)

    @bounded
    async def get(self, event_key: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(notifications).where(notifications.c.event_key == event_key)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("Notification not found")
            return _validate(row)

    @bounded
    async def dispatch(
        self, event_key: str, adapter: TelegramAdapter, at_utc: datetime
    ) -> dict[str, Any]:
        at = utc(at_utc)
        # Disabled delivery leaves the queue intact, so configuration mistakes lose no events.
        if not adapter.enabled:
            return await self.get(event_key)
        claim = str(uuid4())
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(notifications)
                        .where(notifications.c.event_key == event_key)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("Notification not found")
            _validate(row)
            if row["state"] != "pending":
                return dict(row)
            if at < row["received_at_utc"]:
                raise ValueError("Notification attempt clock regression")
            event = Notification.model_validate(row["event"])
            await connection.execute(
                update(notifications)
                .where(notifications.c.event_key == event_key)
                .values(
                    state="delivering",
                    claim_id=claim,
                    attempted_at_utc=at,
                    destination_hash=adapter.destination_hash,
                )
            )
        # This transaction is committed before network I/O. A crash leaves a durable
        # uncertain claim; restart/concurrent dispatch cannot submit it again.
        result = await adapter.send(event)
        return await self._finish(event_key, claim, result, at)

    @bounded
    async def _finish(
        self, event_key: str, claim: str, result: DeliveryResult, at: datetime
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            await connection.execute(
                update(notifications)
                .where(
                    notifications.c.event_key == event_key,
                    notifications.c.claim_id == claim,
                    notifications.c.state == "delivering",
                )
                .values(
                    state=result.state,
                    reason=result.reason,
                    message_id=result.message_id,
                    # A caller-supplied command timestamp, not wall-clock latency evidence.
                    finished_at_utc=at,
                )
            )
        return await self.get(event_key)

    @bounded
    async def recover_unknown(self, event_key: str, at_utc: datetime) -> dict[str, Any]:
        """Explicitly record an abandoned attempt; does not authorize another send."""
        at = utc(at_utc)
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(notifications)
                        .where(notifications.c.event_key == event_key)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("Notification not found")
            _validate(row)
            if row["state"] == "delivering":
                if at < row["attempted_at_utc"]:
                    raise ValueError("Notification recovery clock regression")
                await connection.execute(
                    update(notifications)
                    .where(notifications.c.event_key == event_key)
                    .values(state="unknown", reason="interrupted_attempt", finished_at_utc=at)
                )
        return await self.get(event_key)
