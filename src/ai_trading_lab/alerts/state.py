"""One row per alert key: is it active, and when was the owner last told. Small and bounded: the
keys are a handful of rules times pairs and strategies, and a row is rewritten, never appended."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Table, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data.storage import metadata

alert_state = Table(
    "alert_state",
    metadata,
    Column("key", String(200), primary_key=True),
    Column("rule", String(8), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("title", String(200), nullable=False),
    Column("detail", String(500), nullable=False),
    Column("first_fired_utc", DateTime(timezone=True), nullable=False),
    Column("last_firing_utc", DateTime(timezone=True), nullable=False),  # last check that saw it
    Column("last_sent_utc", DateTime(timezone=True)),  # None: never delivered yet
    Column("last_attempt_utc", DateTime(timezone=True)),  # last try, delivered or not
    Column("resolved_at_utc", DateTime(timezone=True)),  # None: still firing
    Column("episode", Integer, nullable=False),
    Column("failed_sends", Integer, nullable=False, server_default="0"),
)


@dataclass(frozen=True)
class Row:
    key: str
    rule: str
    severity: str
    title: str
    detail: str
    first_fired_utc: datetime
    last_firing_utc: datetime
    last_sent_utc: datetime | None
    last_attempt_utc: datetime | None
    resolved_at_utc: datetime | None
    episode: int
    failed_sends: int


class AlertState:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def rows(self) -> dict[str, Row]:
        async with self.engine.connect() as connection:
            found = (await connection.execute(select(alert_state))).mappings().all()
        return {r["key"]: Row(**dict(r)) for r in found}

    async def save(self, row: Row) -> None:
        values = {
            "rule": row.rule,
            "severity": row.severity,
            "title": row.title[:200],
            "detail": row.detail[:500],
            "first_fired_utc": row.first_fired_utc,
            "last_firing_utc": row.last_firing_utc,
            "last_sent_utc": row.last_sent_utc,
            "last_attempt_utc": row.last_attempt_utc,
            "resolved_at_utc": row.resolved_at_utc,
            "episode": row.episode,
            "failed_sends": row.failed_sends,
        }
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(alert_state)
                .values(key=row.key, **values)
                .on_conflict_do_update(index_elements=[alert_state.c.key], set_=values)
            )

    async def forget_resolved_before(self, cutoff: datetime) -> None:
        """Resolved episodes older than the cutoff carry no information any more."""
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(alert_state).where(alert_state.c.resolved_at_utc < cutoff)
            )
