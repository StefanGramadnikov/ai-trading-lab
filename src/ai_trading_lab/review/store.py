"""Stored reviews and the suggestions they raise.

A review is written once per UTC day and never rewritten. A suggestion is advice: approving or
rejecting it only records the owner's answer. Nothing here changes a strategy, a risk limit or a
configuration; a change is made by a person as a new, versioned experiment configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    String,
    Table,
    Text,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data.storage import metadata
from ai_trading_lab.review.models import DailyReview

MAX_DELIVERY_ATTEMPTS = 6

daily_reviews = Table(
    "daily_reviews",
    metadata,
    Column("day", Date, primary_key=True),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("text", Text, nullable=False),
    Column("delivered_at_utc", DateTime(timezone=True)),
    Column("delivery_attempts", Integer, nullable=False, server_default="0"),
)
review_suggestions = Table(
    "review_suggestions",
    metadata,
    Column("suggestion_id", String(200), primary_key=True),
    Column("day", Date, nullable=False),
    Column("code", String(40), nullable=False),
    Column("strategy_id", String(100), nullable=False, server_default=""),
    Column("text", String(600), nullable=False),
    Column("suggestion", String(800), nullable=False),
    Column("status", String(10), nullable=False, server_default="pending"),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
    Column("decided_at_utc", DateTime(timezone=True)),
    Column("decided_by", String(100)),
    Column("decision_reason", String(300)),
)


@dataclass(frozen=True)
class StoredReview:
    day: date
    text: str
    review: DailyReview
    created_at_utc: datetime
    delivered_at_utc: datetime | None
    delivery_attempts: int


@dataclass(frozen=True)
class Suggestion:
    suggestion_id: str
    day: date
    code: str
    strategy_id: str
    text: str
    suggestion: str
    status: str
    created_at_utc: datetime
    decided_at_utc: datetime | None
    decided_by: str | None
    decision_reason: str | None


def _suggestion(r: Any) -> Suggestion:
    return Suggestion(**{k: r[k] for k in Suggestion.__dataclass_fields__})


class ReviewStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def has(self, day: date) -> bool:
        async with self.engine.connect() as connection:
            found = await connection.scalar(
                select(func.count()).select_from(daily_reviews).where(daily_reviews.c.day == day)
            )
        return bool(found)

    async def save(self, review: DailyReview, text: str, at: datetime) -> bool:
        """Write the day's review once. Returns False if the day already has one."""
        async with self.engine.begin() as connection:
            result = await connection.execute(
                insert(daily_reviews)
                .values(
                    day=review.day,
                    created_at_utc=at,
                    payload=review.model_dump(mode="json"),
                    text=text,
                )
                .on_conflict_do_nothing(index_elements=[daily_reviews.c.day])
            )
        return bool(result.rowcount)

    async def get(self, day: date) -> StoredReview | None:
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(daily_reviews).where(daily_reviews.c.day == day)))
                .mappings()
                .one_or_none()
            )
        return None if row is None else self._stored(row)

    async def latest(self, limit: int = 14) -> list[StoredReview]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(daily_reviews).order_by(daily_reviews.c.day.desc()).limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [self._stored(r) for r in rows]

    @staticmethod
    def _stored(row: Any) -> StoredReview:
        return StoredReview(
            day=row["day"],
            text=row["text"],
            review=DailyReview.model_validate(row["payload"]),
            created_at_utc=row["created_at_utc"],
            delivered_at_utc=row["delivered_at_utc"],
            delivery_attempts=row["delivery_attempts"],
        )

    async def note_attempt(
        self, day: date, delivered: bool, at: datetime, *, attempts: int | None = None
    ) -> None:
        """Count a delivery try (or, with `attempts`, set the count: used to retire a late day)."""
        values: dict[str, Any] = {
            "delivery_attempts": (
                daily_reviews.c.delivery_attempts + 1 if attempts is None else attempts
            )
        }
        if delivered:
            values["delivered_at_utc"] = at
        async with self.engine.begin() as connection:
            await connection.execute(
                update(daily_reviews).where(daily_reviews.c.day == day).values(**values)
            )

    # ---- suggestions -----------------------------------------------------------------------

    async def add_suggestion(
        self, review: DailyReview, code: str, strategy_id: str, text: str, advice: str, at: datetime
    ) -> bool:
        """Raise a suggestion unless the same one is already waiting or was approved.

        A rejected suggestion may come back later, so the owner is asked again if it still holds.
        """
        async with self.engine.begin() as connection:
            existing = await connection.scalar(
                select(func.count())
                .select_from(review_suggestions)
                .where(
                    review_suggestions.c.code == code,
                    review_suggestions.c.strategy_id == strategy_id,
                    review_suggestions.c.status.in_(("pending", "approved")),
                )
            )
            if existing:
                return False
            result = await connection.execute(
                insert(review_suggestions)
                .values(
                    suggestion_id=f"{review.day.isoformat()}:{code}:{strategy_id or '-'}",
                    day=review.day,
                    code=code,
                    strategy_id=strategy_id,
                    text=text[:600],
                    suggestion=advice[:800],
                    status="pending",
                    created_at_utc=at,
                )
                .on_conflict_do_nothing(index_elements=[review_suggestions.c.suggestion_id])
            )
        return bool(result.rowcount)

    async def suggestions(self, status: str | None = None, limit: int = 50) -> list[Suggestion]:
        statement = (
            select(review_suggestions)
            .order_by(review_suggestions.c.created_at_utc.desc())
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(review_suggestions.c.status == status)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_suggestion(r) for r in rows]

    async def pending_count(self) -> int:
        async with self.engine.connect() as connection:
            return int(
                await connection.scalar(
                    select(func.count())
                    .select_from(review_suggestions)
                    .where(review_suggestions.c.status == "pending")
                )
                or 0
            )

    async def decide(
        self, suggestion_id: str, status: str, actor: str, reason: str, at: datetime
    ) -> bool:
        """Record the owner's answer to a pending suggestion; nothing else happens."""
        if status not in ("approved", "rejected"):
            raise ValueError("status must be approved or rejected")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(review_suggestions)
                .where(
                    review_suggestions.c.suggestion_id == suggestion_id,
                    review_suggestions.c.status == "pending",
                )
                .values(
                    status=status,
                    decided_at_utc=at,
                    decided_by=actor[:100],
                    decision_reason=reason[:300],
                )
            )
        return bool(result.rowcount)


def utc_today() -> date:
    return datetime.now(UTC).date()
