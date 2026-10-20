"""Bounded, hash-chained runtime history with deterministic recovery."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.notifications.store import bounded

Reducer = Callable[[dict[str, object], tuple[dict[str, object], ...]], dict[str, object]]

sessions = Table(
    "runtime_sessions",
    metadata,
    Column("session_id", UUID(as_uuid=True), primary_key=True),
    Column("config", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("engine_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("checkpoint", JSONB, nullable=False),
    Column("checkpoint_hash", String(64), nullable=False),
    Column("chain_hash", String(64), nullable=False),
)
events = Table(
    "runtime_events",
    metadata,
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("runtime_sessions.session_id"),
        primary_key=True,
    ),
    Column("sequence", Integer, primary_key=True),
    Column("event_key", String(200), nullable=False),
    Column("event", JSONB, nullable=False),
    Column("checkpoint", JSONB, nullable=False),
    Column("checkpoint_hash", String(64), nullable=False),
    Column("chain_hash", String(64), nullable=False),
    UniqueConstraint("session_id", "event_key", name="uq_runtime_event_key"),
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _copy(value: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("runtime JSON object required")
    result: dict[str, object] = json.loads(_json(value))
    return result


def _at(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("runtime clock must be timezone aware")
    return at.astimezone(UTC).isoformat()


def _stored_at(value: object) -> str:
    try:
        if not isinstance(value, str) or _at(datetime.fromisoformat(value)) != value:
            raise ValueError("noncanonical runtime clock")
        return value
    except (TypeError, ValueError) as error:
        raise ValueError("runtime clock integrity conflict") from error


def _identity(session_id: UUIDValue) -> None:
    if not isinstance(session_id, UUIDValue):
        raise ValueError("runtime session requires native UUID")


def _key(session_id: UUIDValue, namespace: str) -> int:
    return int(content_hash([namespace, str(session_id)])[:16], 16) - (1 << 63)


def _limits(config: dict[str, object]) -> tuple[int, int]:
    cycles, size = config.get("max_cycles", 10000), config.get("max_journal_bytes", 8 * 1024 * 1024)
    if type(cycles) is not int or not 1 <= cycles <= 10000:
        raise ValueError("invalid runtime max_cycles")
    if type(size) is not int or not 1 <= size <= 64 * 1024 * 1024:
        raise ValueError("invalid runtime max_journal_bytes")
    return 2 * cycles + 32, size


def _checkpoint(
    session_id: UUIDValue,
    config: dict[str, object],
    engine_hash: str,
    at: str,
    state: dict[str, object],
    sequence: int,
    previous_bytes: int,
    envelope: object,
) -> dict[str, object]:
    snapshot: dict[str, object] = dict(
        session_id=str(session_id),
        sequence=sequence,
        last_at=at,
        state=_copy(state),
        config_hash=content_hash(config),
        engine_hash=engine_hash,
        journal_bytes=0,
    )
    # Count canonical persisted payloads, including every historical checkpoint.
    base = previous_bytes + len(_json(envelope).encode())
    while True:
        size = base + len(_json(snapshot).encode())
        if snapshot["journal_bytes"] == size:
            return snapshot
        snapshot["journal_bytes"] = size


class Journal:
    def __init__(self, engine: AsyncEngine, reducer: Reducer) -> None:
        self.engine, self.reducer = engine, reducer
        self._connection: AsyncConnection | None = None
        self._worker_session: UUIDValue | None = None
        self._worker_active = False

    @asynccontextmanager
    async def _transaction(
        self, session_id: UUIDValue, *, readonly: bool = False
    ) -> AsyncIterator[AsyncConnection]:
        _identity(session_id)
        if self._connection is None:
            async with self.engine.begin() as connection:
                if readonly:
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                yield connection
            return
        connection = self._connection
        if (
            not self._worker_active
            or self._worker_session != session_id
            or connection.closed
            or connection.invalidated
        ):
            raise ValueError("runtime worker lease is inactive or lost")
        try:
            async with connection.begin():
                if readonly:
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                yield connection
        except (SQLAlchemyError, TimeoutError, asyncio.CancelledError, OSError):
            self._worker_active = False
            await connection.invalidate()
            raise

    async def _lock(self, connection: AsyncConnection, session_id: UUIDValue) -> None:
        _identity(session_id)
        await connection.execute(text("SET LOCAL statement_timeout = '9s'"))
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _key(session_id, "journal")}
        )

    def _reduce(
        self, config: dict[str, object], tape: tuple[dict[str, object], ...]
    ) -> dict[str, object]:
        # A reducer cannot mutate the stored config or a previously verified event.
        return _copy(self.reducer(_copy(config), tuple(_copy(item) for item in tape)))

    async def _load(
        self,
        connection: AsyncConnection,
        session_id: UUIDValue,
    ) -> tuple[dict[str, object], list[dict[str, Any]], dict[str, object]]:
        _identity(session_id)
        row = (
            (await connection.execute(select(sessions).where(sessions.c.session_id == session_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("runtime session not found")
        config: dict[str, object] = row["config"]
        if not isinstance(config, dict) or not isinstance(row["checkpoint"], dict):
            raise ValueError("runtime session schema integrity conflict")
        max_events, max_bytes = _limits(config)
        if (
            row["session_id"] != session_id
            or content_hash(config) != row["config_hash"]
            or row["engine_hash"] != runtime_fingerprint()
        ):
            raise ValueError("runtime config or code integrity conflict")
        created = _stored_at(row["created_at"])
        checkpoint = _checkpoint(
            session_id, config, row["engine_hash"], created, self._reduce(config, ()), 0, 0, config
        )
        chain = content_hash(checkpoint)
        saved = [
            dict(item)
            for item in (
                (
                    await connection.execute(
                        select(events)
                        .where(events.c.session_id == session_id)
                        .order_by(events.c.sequence)
                        .limit(max_events + 1)
                    )
                ).mappings()
            )
        ]
        if len(saved) > max_events:
            raise ValueError("runtime event capacity integrity conflict")
        tape: list[dict[str, object]] = []
        keys: set[str] = set()
        for sequence, entry in enumerate(saved, 1):
            item = entry["event"]
            if not isinstance(item, dict) or set(item) != {"key", "at", "payload"}:
                raise ValueError("runtime event integrity conflict")
            if not isinstance(entry["checkpoint"], dict) or not isinstance(
                entry["checkpoint"].get("state"), dict
            ):
                raise ValueError("runtime checkpoint schema integrity conflict")
            key, at = item["key"], item["at"]
            if (
                entry["session_id"] != session_id
                or entry["sequence"] != sequence
                or not isinstance(key, str)
                or not 1 <= len(key) <= 200
                or key in keys
                or entry["event_key"] != key
                or not isinstance(item["payload"], dict)
                or not isinstance(at, str)
                or _stored_at(at) != at
                or at < str(checkpoint["last_at"])
            ):
                raise ValueError("runtime event identity or clock integrity conflict")
            keys.add(key)
            tape.append(item)
            checkpoint = _checkpoint(
                session_id,
                config,
                row["engine_hash"],
                at,
                entry["checkpoint"]["state"],
                sequence,
                int(str(checkpoint["journal_bytes"])),
                item,
            )
            chain = content_hash([chain, item, content_hash(checkpoint)])
            if (
                entry["checkpoint"] != checkpoint
                or entry["checkpoint_hash"] != content_hash(checkpoint)
                or entry["chain_hash"] != chain
            ):
                raise ValueError("runtime event checkpoint integrity conflict")
        if checkpoint["state"] != self._reduce(config, tuple(tape)):
            raise ValueError("runtime deterministic replay integrity conflict")
        if (
            int(str(checkpoint["journal_bytes"])) > max_bytes
            or row["checkpoint"] != checkpoint
            or row["checkpoint_hash"] != content_hash(checkpoint)
            or row["chain_hash"] != chain
        ):
            raise ValueError("runtime checkpoint integrity conflict")
        return config, saved, checkpoint

    @bounded
    async def create(
        self, session_id: UUIDValue, config: dict[str, object], at: datetime
    ) -> dict[str, object]:
        config, stamp = _copy(config), _at(at)
        _, max_bytes = _limits(config)
        async with self._transaction(session_id) as connection:
            await self._lock(connection, session_id)
            exists = await connection.scalar(
                select(sessions.c.session_id).where(sessions.c.session_id == session_id)
            )
            if exists is not None:
                old, _, checkpoint = await self._load(connection, session_id)
                created = await connection.scalar(
                    select(sessions.c.created_at).where(sessions.c.session_id == session_id)
                )
                if old != config or created != stamp:
                    raise ValueError("runtime session configuration conflict")
                return checkpoint
            engine_hash = runtime_fingerprint()
            checkpoint = _checkpoint(
                session_id, config, engine_hash, stamp, self._reduce(config, ()), 0, 0, config
            )
            if int(str(checkpoint["journal_bytes"])) > max_bytes:
                raise ValueError("runtime journal capacity exceeded")
            digest = content_hash(checkpoint)
            await connection.execute(
                sessions.insert().values(
                    session_id=session_id,
                    config=config,
                    config_hash=content_hash(config),
                    engine_hash=engine_hash,
                    created_at=stamp,
                    checkpoint=checkpoint,
                    checkpoint_hash=digest,
                    chain_hash=digest,
                )
            )
            return checkpoint

    @bounded
    async def append(
        self, session_id: UUIDValue, key: str, payload: dict[str, object], at: datetime
    ) -> dict[str, object]:
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError("invalid runtime event key")
        item: dict[str, object] = dict(key=key, at=_at(at), payload=_copy(payload))
        async with self._transaction(session_id) as connection:
            await self._lock(connection, session_id)
            config, saved, previous = await self._load(connection, session_id)
            for old in saved:
                if old["event_key"] == key:
                    if old["event"] != item:
                        raise ValueError("runtime event key conflict")
                    return dict(old["checkpoint"])
            max_events, max_bytes = _limits(config)
            if len(saved) >= max_events:
                raise ValueError("runtime event capacity exceeded")
            if str(item["at"]) < str(previous["last_at"]):
                raise ValueError("runtime clock moved backwards")
            tape = tuple(entry["event"] for entry in saved) + (item,)
            checkpoint = _checkpoint(
                session_id,
                config,
                str(previous["engine_hash"]),
                str(item["at"]),
                self._reduce(config, tape),
                len(tape),
                int(str(previous["journal_bytes"])),
                item,
            )
            if int(str(checkpoint["journal_bytes"])) > max_bytes:
                raise ValueError("runtime journal capacity exceeded")
            prior_chain = saved[-1]["chain_hash"] if saved else content_hash(previous)
            digest = content_hash(checkpoint)
            chain = content_hash([prior_chain, item, digest])
            await connection.execute(
                events.insert().values(
                    session_id=session_id,
                    sequence=len(tape),
                    event_key=key,
                    event=item,
                    checkpoint=checkpoint,
                    checkpoint_hash=digest,
                    chain_hash=chain,
                )
            )
            await connection.execute(
                sessions.update()
                .where(sessions.c.session_id == session_id)
                .values(checkpoint=checkpoint, checkpoint_hash=digest, chain_hash=chain)
            )
            return checkpoint

    @bounded
    async def get(self, session_id: UUIDValue) -> dict[str, object]:
        async with self._transaction(session_id, readonly=True) as connection:
            await self._lock(connection, session_id)
            return (await self._load(connection, session_id))[2]

    @bounded
    async def config(self, session_id: UUIDValue) -> dict[str, object]:
        async with self._transaction(session_id, readonly=True) as connection:
            await self._lock(connection, session_id)
            return _copy((await self._load(connection, session_id))[0])

    @asynccontextmanager
    async def worker(self, session_id: UUIDValue) -> AsyncIterator["Journal"]:
        """Dedicated connection owns a session until exit, including cancellation."""
        _identity(session_id)
        if self._connection is not None:
            raise ValueError("runtime worker is already bound")
        async with asyncio.timeout(10):
            connection = await self.engine.connect()
        acquired = False
        active = Journal(self.engine, self.reducer)
        active._connection = connection
        active._worker_session = session_id
        try:
            try:
                async with asyncio.timeout(10):
                    acquired = bool(
                        await connection.scalar(
                            text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": _key(session_id, "worker")},
                        )
                    )
            except BaseException:
                # A cancelled response may hide a successfully acquired session lock.
                # Discard the physical connection rather than return it to the pool.
                await connection.invalidate()
                raise
            if not acquired:
                raise ValueError("runtime worker already owned")
            # A session lock survives transaction commit; all worker reads/writes
            # now use this physical connection, without replacement or retries.
            async with asyncio.timeout(10):
                await connection.commit()
            active._worker_active = True
            yield active
        finally:
            active._worker_active = False
            try:
                if acquired and not connection.invalidated and not connection.closed:
                    async with asyncio.timeout(10):
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": _key(session_id, "worker")},
                        )
            except BaseException:
                await connection.invalidate()
                raise
            finally:
                async with asyncio.timeout(10):
                    await connection.close()
