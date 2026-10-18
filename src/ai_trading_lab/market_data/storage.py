from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data.models import Batch, Capture, DataQuality, Observation, Source

metadata = MetaData()
raw_events = Table(
    "raw_source_events",
    metadata,
    Column("capture_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("source", String, nullable=False),
    Column("data_mode", String, nullable=False),
    Column("received_at_utc", DateTime(timezone=True), nullable=False),
    Column("body_hash", String(64), nullable=False),
    Column("envelope_hash", String(64), nullable=False),
    Column("envelope", JSONB, nullable=False),
    Column("body", Text, nullable=False),
)
markets = Table(
    "markets",
    metadata,
    Column("definition_hash", String(64), primary_key=True),
    Column("source", String, nullable=False),
    Column("venue", String, nullable=False),
    Column("native_id", String, nullable=False),
    Column("definition", JSONB, nullable=False),
)
snapshots = Table(
    "market_snapshots",
    metadata,
    Column("snapshot_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "capture_id",
        PG_UUID(as_uuid=True),
        ForeignKey("raw_source_events.capture_id"),
        nullable=False,
    ),
    Column("definition_hash", String(64), ForeignKey("markets.definition_hash"), nullable=False),
    Column("event_time_utc", DateTime(timezone=True)),
    Column("received_at_utc", DateTime(timezone=True), nullable=False),
    Column("quality", String, nullable=False),
    Column("price", Numeric),
    Column("liquidity", Numeric),
    Column("snapshot_hash", String(64), nullable=False),
    Column("observation", JSONB, nullable=False),
)
runs = Table(
    "ingestion_runs",
    metadata,
    Column("run_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("source", String, nullable=False),
    Column("recorded_at_utc", DateTime(timezone=True), nullable=False),
    Column("status", String, nullable=False),
    Column("capture_count", Integer, nullable=False),
    Column("observation_count", Integer, nullable=False),
    Column("failures", JSONB, nullable=False),
)


Index("ix_markets_identity", markets.c.source, markets.c.venue, markets.c.native_id)
Index("ix_snapshots_market_time", snapshots.c.definition_hash, snapshots.c.received_at_utc)


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class RunSummary:
    run_id: UUID
    status: str
    captures_added: int
    observations_added: int
    failure_count: int


class ObservationStore:
    """Atomic append-only writes; a redelivery ID cannot rewrite existing evidence."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def save(self, source: Source, batch: Batch) -> RunSummary:
        capture_map: dict[UUID, Capture] = {}
        for capture in batch.captures:
            if capture.source != source:
                raise ValueError("Capture source does not match ingestion source")
            if capture.capture_id in capture_map and capture_map[capture.capture_id] != capture:
                raise ValueError("Conflicting duplicate capture ID")
            capture_map[capture.capture_id] = capture
        observations = []
        for item in batch.observations:
            matching_capture = capture_map.get(item.capture_id)
            if (
                matching_capture is None
                or matching_capture.status_code != 200
                or item.instrument.source != source
                or item.received_at_utc != matching_capture.received_at_utc
            ):
                raise ValueError("Observation does not match its successful source capture")
            observations.append(item.assessed())
        if any(f.source != source for f in batch.failures):
            raise ValueError("Failure source does not match ingestion source")
        status = "empty"
        if observations:
            status = (
                "partial"
                if batch.failures or any(o.quality != DataQuality.VALID for o in observations)
                else "complete"
            )
        elif batch.failures:
            status = "failed"
        run_id = uuid4()
        capture_count = snapshot_count = 0
        async with self.engine.begin() as connection:
            for capture in capture_map.values():
                envelope = capture.model_dump(mode="json")
                digest = content_hash(envelope)
                added = await connection.scalar(
                    insert(raw_events)
                    .values(
                        capture_id=capture.capture_id,
                        source=source.value,
                        data_mode=capture.data_mode.value,
                        received_at_utc=capture.received_at_utc,
                        body_hash=capture.fingerprint,
                        envelope_hash=digest,
                        envelope=envelope,
                        body=capture.body,
                    )
                    .on_conflict_do_nothing(index_elements=[raw_events.c.capture_id])
                    .returning(raw_events.c.capture_id)
                )
                if added is None:
                    existing = await connection.scalar(
                        select(raw_events.c.envelope_hash).where(
                            raw_events.c.capture_id == capture.capture_id
                        )
                    )
                    if existing != digest:
                        raise ValueError("Capture ID collision: immutable evidence differs")
                else:
                    capture_count += 1
            for observation in observations:
                instrument = observation.instrument
                await connection.execute(
                    insert(markets)
                    .values(
                        definition_hash=instrument.definition_hash,
                        source=instrument.source.value,
                        venue=instrument.venue,
                        native_id=instrument.native_id,
                        definition=instrument.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
                payload = observation.model_dump(mode="json")
                digest = content_hash(payload)
                snapshot_id = uuid5(
                    observation.capture_id,
                    instrument.definition_hash + ":" + observation.schema_version,
                )
                added = await connection.scalar(
                    insert(snapshots)
                    .values(
                        snapshot_id=snapshot_id,
                        capture_id=observation.capture_id,
                        definition_hash=instrument.definition_hash,
                        event_time_utc=observation.event_time_utc,
                        received_at_utc=observation.received_at_utc,
                        quality=observation.quality.value,
                        price=observation.price,
                        liquidity=observation.liquidity,
                        snapshot_hash=digest,
                        observation=payload,
                    )
                    .on_conflict_do_nothing()
                    .returning(snapshots.c.snapshot_id)
                )
                if added is None:
                    existing = await connection.scalar(
                        select(snapshots.c.snapshot_hash).where(
                            snapshots.c.snapshot_id == snapshot_id
                        )
                    )
                    if existing != digest:
                        raise ValueError("Snapshot ID collision: immutable evidence differs")
                else:
                    snapshot_count += 1
            await connection.execute(
                insert(runs).values(
                    run_id=run_id,
                    source=source.value,
                    recorded_at_utc=datetime.now(UTC),
                    status=status,
                    capture_count=capture_count,
                    observation_count=snapshot_count,
                    failures=[f.model_dump(mode="json") for f in batch.failures],
                )
            )
        return RunSummary(run_id, status, capture_count, snapshot_count, len(batch.failures))

    async def read(self, capture_id: UUID) -> tuple[Observation, ...]:
        async with self.engine.connect() as connection:
            payloads = (
                (
                    await connection.execute(
                        select(snapshots.c.observation)
                        .where(snapshots.c.capture_id == capture_id)
                        .order_by(snapshots.c.snapshot_id)
                    )
                )
                .scalars()
                .all()
            )
        return tuple(Observation.model_validate(value) for value in payloads)
