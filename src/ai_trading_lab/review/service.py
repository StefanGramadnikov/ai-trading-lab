"""Gathers one UTC day's facts from the tables the loop writes, then builds, stores and delivers
its review. Reads never lock anything the trading loop needs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore, CallFact
from ai_trading_lab.config import Settings
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.live.store import LiveStore
from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.review import digest
from ai_trading_lab.review.models import DailyReview
from ai_trading_lab.review.store import MAX_DELIVERY_ATTEMPTS, ReviewStore

DAY = timedelta(days=1)
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
REVIEW_AFTER = timedelta(minutes=30)  # a day is reviewed this long after it ends
CATCH_UP_DAYS = 2  # earlier days that never got a review (the job was not running yet)

Deliver = Callable[[Notification], Awaitable[bool]]


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class ReviewService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.settings, self.clock = engine, settings, clock
        self.live = LiveStore(engine)
        self.store = ReviewStore(engine)
        self.budget = BudgetStore(engine, settings)
        self.journal = ErrorJournal(engine, clock=clock)
        self._empty: set[date] = set()  # days known to have no data at all

    # ---- building ---------------------------------------------------------------------------

    @asynccontextmanager
    async def _snapshot(self) -> AsyncIterator[AsyncConnection]:
        """One read-only, repeatable view: the AI loop may commit between two plain reads, and the
        budget ledger's integrity check would then see a half-written history."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            yield connection

    async def build(self, day: date, now: datetime) -> DailyReview | None:
        """The review of one UTC day, or None when the loop recorded nothing in it."""
        start, end = day_start(day), day_start(day) + DAY
        trailing_start = end - digest.TRAILING_DAYS * DAY
        rows = sorted(
            await self.live.strategies(enabled_only=False),
            key=lambda r: (digest.ORDER.get(r.kind, 9), r.strategy_id),
        )
        days = []
        entries: dict[str, int] = {}
        ai_decisions: list[Any] = []
        ai_trailing: list[Any] = []
        cycles_seen = 0
        first_seen: datetime | None = None
        for row in rows:
            points = await self.live.equity_between(row.strategy_id, start, end)
            decisions = await self.live.decisions_between(row.strategy_id, start, end)
            cycles_seen = max(cycles_seen, sum(1 for p in points if start <= p.at_utc < end))
            first = await self.live.first_equity_at(row.strategy_id)
            if first is not None and (first_seen is None or first < first_seen):
                first_seen = first
            days.append(
                digest.summarise_strategy(
                    strategy_id=row.strategy_id,
                    kind=row.kind,
                    points=points,
                    decisions=decisions,
                    start=start,
                    end=end,
                )
            )
            week = await self.live.decisions_between(row.strategy_id, trailing_start, end)
            entries[row.strategy_id] = sum(
                1 for d in week if d.action == "buy" and d.status in ("filled", "partial")
            )
            if row.kind == "ai":
                ai_decisions, ai_trailing = decisions, week
        if cycles_seen == 0 and all(s.decisions == 0 for s in days):
            # Before the loop ever ran there is nothing to say. After it started, a day without a
            # single cycle is exactly the day the owner needs to hear about.
            if first_seen is None or end <= first_seen:
                return None
        limit = self.settings.ai_total_limit_usd
        async with self._snapshot() as snapshot:
            facts: list[CallFact] = []
            week_calls: list[CallFact] = []
            total_spent = Decimal(0)
            for budget_id in await self.budget.budget_ids():
                ever = await self.budget.calls_between(budget_id, EPOCH, end, snapshot=snapshot)
                total_spent += sum((c.amount + c.held for c in ever), Decimal(0))
                facts += [c for c in ever if start <= c.at < end]
                week_calls += [c for c in ever if trailing_start <= c.at < end]
        ai = digest.summarise_ai(
            active=self.settings.ai_enabled and limit > 0,
            calls=facts,
            decisions=ai_decisions,
            total_spent=total_spent,
            total_limit=limit,
        )
        run_days = (
            Decimal(0)
            if first_seen is None
            else Decimal(max((end - first_seen).total_seconds(), 0)) / Decimal(86400)
        )
        trailing = digest.Trailing(
            run_days=run_days,
            entries=entries,
            ai_calls=len(week_calls),
            ai_abstains=sum(1 for d in ai_trailing if d.reason == "ai_abstain"),
        )
        errors = await self.journal.summary(now)
        expected = digest.expected_cycles(first_seen, start, end)
        return DailyReview(
            day=day,
            generated_at=now,
            window_start=start,
            window_end=end,
            cycles_seen=cycles_seen,
            cycles_expected=expected,
            errors_24h=errors.errors_24h,
            warnings_24h=errors.warnings_24h,
            unacknowledged_errors=errors.unacknowledged_errors,
            strategies=days,
            ai=ai,
            observations=digest.observe(
                strategies=days,
                ai=ai,
                cycles_seen=cycles_seen,
                cycles_expected=expected,
                trailing=trailing,
            ),
        )

    # ---- storing and sending ----------------------------------------------------------------

    async def create(self, day: date, now: datetime) -> DailyReview | None:
        """Build and store the day's review once (a stored review is never rewritten)."""
        review = await self.build(day, now)
        if review is None:
            return None
        for o in review.observations:
            if o.suggestion:
                await self.store.add_suggestion(
                    review, o.code, o.strategy_id or "", o.text, o.suggestion, now
                )
        text = digest.render_text(review, await self.store.pending_count())
        await self.store.save(review, text, now)
        return review

    async def run_due(self, now: datetime, deliver: Deliver | None) -> dict[str, Any]:
        """Create the reviews that are due and send the newest one; safe to call every tick."""
        latest = (now - REVIEW_AFTER).date() - timedelta(days=1)
        report: dict[str, Any] = {"day": latest.isoformat(), "created": False, "sent": None}
        for back in range(CATCH_UP_DAYS, -1, -1):
            day = latest - timedelta(days=back)
            if day in self._empty or await self.store.has(day):
                continue
            made = await self.create(day, now)
            if made is None:
                self._empty.add(day)
                continue
            report["created"] = True
            if day != latest:  # an older day is kept for reference, never sent late
                await self.store.note_attempt(day, False, now, attempts=MAX_DELIVERY_ATTEMPTS)
        stored = await self.store.get(latest)
        if (
            deliver is not None
            and stored is not None
            and stored.delivered_at_utc is None
            and stored.delivery_attempts < MAX_DELIVERY_ATTEMPTS
        ):
            # The outbox tries each key once, so every attempt gets its own key; the event time is
            # fixed (when the review was written) so the same text is the same event.
            try:
                event = Notification(
                    event_key=f"review:{latest.isoformat()}:{stored.delivery_attempts + 1}",
                    kind="daily_summary",
                    occurred_at_utc=stored.created_at_utc,
                    text=stored.text,
                )
            except ValidationError:
                # A message the outbox would never accept must not be retried every five minutes.
                await self.store.note_attempt(latest, False, now, attempts=MAX_DELIVERY_ATTEMPTS)
                report["sent"] = False
                return report
            delivered = await deliver(event)
            await self.store.note_attempt(latest, delivered, now)
            report["sent"] = delivered
        return report
