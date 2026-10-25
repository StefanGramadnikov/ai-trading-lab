"""Durable live-loop state: strategies, closed candles, planned/finished decisions, state, equity.

A decision that places an order is written as ``planned`` (with its intent and raw evidence)
before the order is submitted and finished afterwards. A crash in between is recovered by
resubmitting the same idempotency key, which the paper broker answers without a second fill.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.live.risk import RiskState
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import content_hash, metadata

PRICE = Numeric(38, 18)

live_strategies = Table(
    "live_strategies",
    metadata,
    Column("strategy_id", String(100), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("portfolio_id", PG_UUID(as_uuid=True), nullable=False, unique=True),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("config", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
)
live_candles = Table(
    "live_candles",
    metadata,
    Column("pair", String(12), primary_key=True),
    Column("interval_minutes", Integer, primary_key=True),
    Column("open_time_utc", DateTime(timezone=True), primary_key=True),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("vwap", PRICE, nullable=False),
    Column("volume", PRICE, nullable=False),
    Column("trades", Integer, nullable=False),
)
live_decisions = Table(
    "live_decisions",
    metadata,
    Column("decision_id", PG_UUID(as_uuid=True), primary_key=True),
    # Insertion order: timestamps can tie, and "what happened first" must be unambiguous.
    Column("seq", BigInteger, Identity(always=True), nullable=False),
    Column("strategy_id", String(100), nullable=False),
    Column("pair", String(12), nullable=False),
    Column("cycle_at_utc", DateTime(timezone=True), nullable=False),
    Column("action", String(10), nullable=False),
    Column("reason", String(100), nullable=False),
    Column("status", String(12), nullable=False),
    Column("idempotency_key", String(200)),
    Column("intent", JSONB),
    Column("evidence", JSONB),
    Column("result", JSONB),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
    Column("finished_at_utc", DateTime(timezone=True)),
    UniqueConstraint("strategy_id", "idempotency_key", name="uq_live_decision_key"),
)
Index(
    "ix_live_decisions_strategy_time", live_decisions.c.strategy_id, live_decisions.c.cycle_at_utc
)
live_state = Table(
    "live_state",
    metadata,
    Column("strategy_id", String(100), primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("updated_at_utc", DateTime(timezone=True), nullable=False),
)
live_equity = Table(
    "live_equity",
    metadata,
    Column("strategy_id", String(100), primary_key=True),
    Column("at_utc", DateTime(timezone=True), primary_key=True),
    Column("equity", PRICE, nullable=False),
    Column("cash", PRICE, nullable=False),
    Column("positions_value", PRICE, nullable=False),
)

Status = Literal["planned", "filled", "partial", "rejected", "skipped"]


class LoopState(FrozenModel):
    """Everything the loop needs beyond the paper ledger, kept per strategy."""

    risk: RiskState
    entered_at: dict[str, AwareDatetime] = Field(default_factory=dict)
    last_entry_at: dict[str, AwareDatetime] = Field(default_factory=dict)
    last_stop_at: dict[str, AwareDatetime] = Field(default_factory=dict)


class StrategyRow(FrozenModel):
    strategy_id: str
    kind: str
    portfolio_id: UUID
    enabled: bool
    config: dict[str, Any]


class PlannedDecision(FrozenModel):
    decision_id: UUID
    status: str
    created: bool
    pair: str = ""
    action: str = ""
    reason: str = ""
    cycle_at_utc: AwareDatetime | None = None
    idempotency_key: str | None = None
    intent: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None


class DecisionRow(FrozenModel):
    decision_id: UUID
    strategy_id: str
    pair: str
    cycle_at_utc: AwareDatetime
    action: str
    reason: str
    status: str
    result: dict[str, Any] | None = None


class EquityPoint(FrozenModel):
    at_utc: AwareDatetime
    equity: Decimal
    cash: Decimal
    positions_value: Decimal


class LiveStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # ---- strategies -------------------------------------------------------------------

    async def register(
        self, strategy_id: str, kind: str, portfolio_id: UUID, config: dict[str, Any], at: datetime
    ) -> None:
        digest = content_hash(config)
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(live_strategies)
                .values(
                    strategy_id=strategy_id,
                    kind=kind,
                    portfolio_id=portfolio_id,
                    config=config,
                    config_hash=digest,
                    created_at_utc=at,
                )
                .on_conflict_do_nothing()
            )
            row = (
                (
                    await connection.execute(
                        select(live_strategies).where(live_strategies.c.strategy_id == strategy_id)
                    )
                )
                .mappings()
                .one()
            )
            if (
                row["config_hash"] != digest
                or row["portfolio_id"] != portfolio_id
                or row["kind"] != kind
            ):
                raise ValueError("strategy registration is immutable")

    async def strategies(self, *, enabled_only: bool = True) -> list[StrategyRow]:
        statement = select(live_strategies).order_by(live_strategies.c.strategy_id)
        if enabled_only:
            statement = statement.where(live_strategies.c.enabled.is_(True))
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            StrategyRow(
                strategy_id=r["strategy_id"],
                kind=r["kind"],
                portfolio_id=r["portfolio_id"],
                enabled=r["enabled"],
                config=r["config"],
            )
            for r in rows
        ]

    async def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        async with self.engine.begin() as connection:
            done = await connection.execute(
                update(live_strategies)
                .where(live_strategies.c.strategy_id == strategy_id)
                .values(enabled=enabled)
            )
            if done.rowcount != 1:
                raise ValueError("unknown strategy")

    # ---- candles ----------------------------------------------------------------------

    async def upsert_candles(self, pair: str, candles: list[Candle]) -> None:
        """Closed candles are kept as first seen; a repeat fetch changes nothing."""
        if not candles:
            return
        rows = [
            {
                "pair": pair,
                "interval_minutes": c.interval_minutes,
                "open_time_utc": c.open_time_utc,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "vwap": c.vwap,
                "volume": c.volume,
                "trades": c.trades,
            }
            for c in candles
        ]
        async with self.engine.begin() as connection:
            await connection.execute(insert(live_candles).on_conflict_do_nothing(), rows)

    async def candles(self, pair: str, interval_minutes: int, limit: int) -> list[Candle]:
        from datetime import timedelta

        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(live_candles)
                        .where(
                            live_candles.c.pair == pair,
                            live_candles.c.interval_minutes == interval_minutes,
                        )
                        .order_by(live_candles.c.open_time_utc.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [
            Candle(
                open_time_utc=r["open_time_utc"],
                close_time_utc=r["open_time_utc"] + timedelta(minutes=interval_minutes),
                interval_minutes=interval_minutes,
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                vwap=r["vwap"],
                volume=r["volume"],
                trades=r["trades"],
            )
            for r in reversed(rows)
        ]

    async def last_open_time(self, pair: str, interval_minutes: int) -> datetime | None:
        async with self.engine.connect() as connection:
            value = await connection.scalar(
                select(live_candles.c.open_time_utc)
                .where(
                    live_candles.c.pair == pair,
                    live_candles.c.interval_minutes == interval_minutes,
                )
                .order_by(live_candles.c.open_time_utc.desc())
                .limit(1)
            )
        return value if isinstance(value, datetime) else None

    # ---- decisions --------------------------------------------------------------------

    async def plan_decision(
        self,
        strategy_id: str,
        pair: str,
        cycle_at: datetime,
        action: str,
        reason: str,
        key: str,
        intent: dict[str, Any],
        evidence: dict[str, Any],
    ) -> PlannedDecision:
        """Record an order decision before it is submitted; idempotent on its key."""
        decision_id = uuid4()
        async with self.engine.begin() as connection:
            added = await connection.scalar(
                insert(live_decisions)
                .values(
                    decision_id=decision_id,
                    strategy_id=strategy_id,
                    pair=pair,
                    cycle_at_utc=cycle_at,
                    action=action,
                    reason=reason,
                    status="planned",
                    idempotency_key=key,
                    intent=intent,
                    evidence=evidence,
                    created_at_utc=cycle_at,
                )
                .on_conflict_do_nothing(constraint="uq_live_decision_key")
                .returning(live_decisions.c.decision_id)
            )
            row = (
                (
                    await connection.execute(
                        select(live_decisions).where(
                            live_decisions.c.strategy_id == strategy_id,
                            live_decisions.c.idempotency_key == key,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return PlannedDecision(
            decision_id=row["decision_id"],
            status=row["status"],
            created=added is not None,
            pair=row["pair"],
            action=row["action"],
            reason=row["reason"],
            cycle_at_utc=row["cycle_at_utc"],
            idempotency_key=key,
            intent=row["intent"],
            evidence=row["evidence"],
        )

    async def finish_decision(
        self, decision_id: UUID, status: Status, result: dict[str, Any], at: datetime
    ) -> None:
        async with self.engine.begin() as connection:
            done = await connection.execute(
                update(live_decisions)
                .where(
                    live_decisions.c.decision_id == decision_id,
                    live_decisions.c.status == "planned",
                )
                .values(status=status, result=result, finished_at_utc=at)
            )
            if done.rowcount != 1:
                raise ValueError("decision already finished or unknown")

    async def log_decision(
        self, strategy_id: str, pair: str, cycle_at: datetime, action: str, reason: str
    ) -> None:
        """A decision that places no order (hold, blocked, no data)."""
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(live_decisions).values(
                    decision_id=uuid4(),
                    strategy_id=strategy_id,
                    pair=pair,
                    cycle_at_utc=cycle_at,
                    action=action,
                    reason=reason,
                    status="skipped",
                    created_at_utc=cycle_at,
                    finished_at_utc=cycle_at,
                )
            )

    async def planned(self, strategy_id: str) -> list[PlannedDecision]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(live_decisions)
                        .where(
                            live_decisions.c.strategy_id == strategy_id,
                            live_decisions.c.status == "planned",
                        )
                        .order_by(live_decisions.c.seq)
                    )
                )
                .mappings()
                .all()
            )
        return [
            PlannedDecision(
                decision_id=r["decision_id"],
                status=r["status"],
                created=False,
                pair=r["pair"],
                action=r["action"],
                reason=r["reason"],
                cycle_at_utc=r["cycle_at_utc"],
                idempotency_key=r["idempotency_key"],
                intent=r["intent"],
                evidence=r["evidence"],
            )
            for r in rows
        ]

    async def decision_count(self, strategy_id: str) -> int:
        async with self.engine.connect() as connection:
            count = await connection.scalar(
                select(func.count())
                .select_from(live_decisions)
                .where(live_decisions.c.strategy_id == strategy_id)
            )
        return int(count or 0)

    async def recent_decisions(self, strategy_id: str, limit: int) -> list[DecisionRow]:
        """The newest `limit` decisions, oldest first."""
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(live_decisions)
                        .where(live_decisions.c.strategy_id == strategy_id)
                        .order_by(live_decisions.c.seq.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [self._row(r) for r in reversed(rows)]

    @staticmethod
    def _row(r: Any) -> DecisionRow:
        return DecisionRow(
            decision_id=r["decision_id"],
            strategy_id=r["strategy_id"],
            pair=r["pair"],
            cycle_at_utc=r["cycle_at_utc"],
            action=r["action"],
            reason=r["reason"],
            status=r["status"],
            result=r["result"],
        )

    async def decisions(self, strategy_id: str, limit: int = 1000) -> list[DecisionRow]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(live_decisions)
                        .where(live_decisions.c.strategy_id == strategy_id)
                        .order_by(live_decisions.c.seq)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [
            DecisionRow(
                decision_id=r["decision_id"],
                strategy_id=r["strategy_id"],
                pair=r["pair"],
                cycle_at_utc=r["cycle_at_utc"],
                action=r["action"],
                reason=r["reason"],
                status=r["status"],
                result=r["result"],
            )
            for r in rows
        ]

    # ---- state and equity -------------------------------------------------------------

    async def load_state(self, strategy_id: str) -> LoopState | None:
        async with self.engine.connect() as connection:
            row = await connection.scalar(
                select(live_state.c.state).where(live_state.c.strategy_id == strategy_id)
            )
        return LoopState.model_validate(row) if row is not None else None

    async def save_state(self, strategy_id: str, state: LoopState, at: datetime) -> None:
        payload = state.model_dump(mode="json")
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(live_state)
                .values(strategy_id=strategy_id, state=payload, updated_at_utc=at)
                .on_conflict_do_update(
                    index_elements=[live_state.c.strategy_id],
                    set_={"state": payload, "updated_at_utc": at},
                )
            )

    async def record_equity(
        self, strategy_id: str, at: datetime, equity: Decimal, cash: Decimal, positions: Decimal
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(live_equity)
                .values(
                    strategy_id=strategy_id,
                    at_utc=at,
                    equity=equity,
                    cash=cash,
                    positions_value=positions,
                )
                .on_conflict_do_nothing()
            )

    async def latest_equity(self, strategy_id: str) -> EquityPoint | None:
        async with self.engine.connect() as connection:
            r = (
                (
                    await connection.execute(
                        select(live_equity)
                        .where(live_equity.c.strategy_id == strategy_id)
                        .order_by(live_equity.c.at_utc.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if r is None:
            return None
        return EquityPoint(
            at_utc=r["at_utc"],
            equity=r["equity"],
            cash=r["cash"],
            positions_value=r["positions_value"],
        )

    async def equity_curve(self, strategy_id: str, limit: int = 5000) -> list[EquityPoint]:
        """The newest `limit` equity points, oldest first."""
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(live_equity)
                        .where(live_equity.c.strategy_id == strategy_id)
                        .order_by(live_equity.c.at_utc.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [
            EquityPoint(
                at_utc=r["at_utc"],
                equity=r["equity"],
                cash=r["cash"],
                positions_value=r["positions_value"],
            )
            for r in reversed(rows)
        ]
