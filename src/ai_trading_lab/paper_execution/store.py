"""Transactional offline paper portfolios; no venue execution capability."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid5

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.paper_execution.accounting import (
    JournalLine,
    PortfolioState,
    open_portfolio,
)
from ai_trading_lab.paper_execution.history import (
    Effect,
    History,
    OrderReceipt,
    clock,
    request_hash,
)
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    PaperInstrument,
    SimulationPolicy,
    decimal_amount,
)
from ai_trading_lab.paper_execution.replay import ReplayManifest

portfolios = Table(
    "paper_portfolios",
    metadata,
    Column("portfolio_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("config", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("state", JSONB, nullable=False),
    Column("last_event_at", DateTime(timezone=True)),
)
orders = Table(
    "paper_orders",
    metadata,
    Column("order_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        nullable=False,
    ),
    Column("request_hash", String(64), nullable=False),
    Column("receipt", JSONB, nullable=False),
    Column("receipt_hash", String(64), nullable=False),
)
journal = Table(
    "paper_journal",
    metadata,
    Column("entry_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        nullable=False,
    ),
    Column("command_id", PG_UUID(as_uuid=True), nullable=False),
    Column("account", String, nullable=False),
    Column("unit", String, nullable=False),
    Column("amount", Numeric, nullable=False),
)
events = Table(
    "paper_events",
    metadata,
    Column("event_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
)
fills = Table(
    "paper_fills",
    metadata,
    Column(
        "order_id", PG_UUID(as_uuid=True), ForeignKey("paper_orders.order_id"), primary_key=True
    ),
    Column("quantity", Numeric, nullable=False),
    Column("notional", Numeric, nullable=False),
    Column("fee", Numeric, nullable=False),
)
liquidity = Table(
    "paper_liquidity",
    metadata,
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        primary_key=True,
    ),
    Column("snapshot_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("quote_hash", String(64), nullable=False),
    Column("quote", JSONB, nullable=False),
    Column("buy", JSONB, nullable=False),
    Column("sell", JSONB, nullable=False),
)
instruments = Table(
    "paper_instruments",
    metadata,
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        primary_key=True,
    ),
    Column("identity", String(64), primary_key=True),
    Column("definition", JSONB, nullable=False),
    Column("settled", Integer, nullable=False),
)
commands = Table(
    "paper_commands",
    metadata,
    Column("command_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "portfolio_id",
        PG_UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        nullable=False,
    ),
    Column("request_hash", String(64), nullable=False),
    Column("state", JSONB, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("key", String, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
)


def projections(history: History) -> dict[str, list[dict[str, object]]]:
    """All materialized rows, derived independently of the stored projections."""
    pid = history.state.portfolio_id
    result: dict[str, list[dict[str, object]]] = {
        table.name: []
        for table in (orders, journal, events, fills, liquidity, instruments, commands)
    }

    def add_lines(command_id: UUID, lines: tuple[JournalLine, ...]) -> None:
        for i, line in enumerate(lines):
            result[journal.name].append(
                dict(
                    entry_id=uuid5(command_id, str(i)),
                    portfolio_id=pid,
                    command_id=command_id,
                    **line.model_dump(),
                )
            )

    add_lines(uuid5(pid, "funding"), history.funding)
    for effect in history.effects.values():
        command_id = effect.command_id
        result[commands.name].append(
            dict(
                command_id=command_id,
                portfolio_id=pid,
                request_hash=request_hash(effect.kind, effect.payload),
                state=effect.state.model_dump(mode="json"),
                sequence=effect.state.sequence,
                kind=effect.kind,
                key=effect.key,
                at=effect.submitted_at,
                payload=effect.payload,
            )
        )
        add_lines(command_id, effect.lines)
        start = effect.state.sequence - len(effect.events)
        for i, (kind, payload) in enumerate(effect.events, 1):
            result[events.name].append(
                dict(
                    event_id=uuid5(command_id, "event:" + str(i)),
                    portfolio_id=pid,
                    sequence=start + i,
                    kind=kind,
                    at=effect.at,
                    payload=payload,
                )
            )
        if effect.receipt is not None:
            payload = effect.receipt.model_dump(mode="json")
            result[orders.name].append(
                dict(
                    order_id=command_id,
                    portfolio_id=pid,
                    request_hash=effect.receipt.intent.fingerprint,
                    receipt=payload,
                    receipt_hash=content_hash(payload),
                )
            )
            fill = effect.receipt.result
            if fill.quantity:
                result[fills.name].append(
                    dict(
                        order_id=command_id,
                        quantity=fill.quantity,
                        notional=fill.notional,
                        fee=fill.fee,
                    )
                )
    for snapshot_id, (quote, buy, sell) in history.quotes.items():
        payload = quote.model_dump(mode="json")
        result[liquidity.name].append(
            dict(
                portfolio_id=pid,
                snapshot_id=snapshot_id,
                quote_hash=content_hash(payload),
                quote=payload,
                buy=[str(x) for x in buy],
                sell=[str(x) for x in sell],
            )
        )
    for native, (instrument, settled) in history.definitions.items():
        result[instruments.name].append(
            dict(
                portfolio_id=pid,
                identity=native,
                definition=instrument.model_dump(mode="json"),
                settled=int(settled),
            )
        )
    if any(len(rows) > 10000 for rows in result.values()):
        raise ValueError("Paper history exceeds bounded scenario capacity")
    return result


class PaperStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(
        self,
        portfolio_id: UUID,
        cash_unit: str,
        cash_step: Decimal,
        initial_cash: Decimal,
        policy: SimulationPolicy,
        *,
        replay_manifest: ReplayManifest | None = None,
    ) -> PortfolioState:
        state, _ = open_portfolio(portfolio_id, cash_unit, cash_step, initial_cash)
        config: dict[str, object] = dict(
            cash_unit=cash_unit,
            cash_step=str(cash_step),
            initial_cash=str(initial_cash),
            policy=policy.model_dump(mode="json"),
        )
        if replay_manifest is not None:
            config["replay_manifest"] = ReplayManifest.model_validate(
                replay_manifest.model_dump(mode="json")
            ).model_dump(mode="json")
        async with self.engine.begin() as connection:
            added = await connection.scalar(
                insert(portfolios)
                .values(
                    portfolio_id=portfolio_id,
                    config=config,
                    config_hash=content_hash(config),
                    state=state.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
                .returning(portfolios.c.portfolio_id)
            )
            if added is not None:
                history = History(portfolio_id, config)
                await connection.execute(insert(journal), projections(history)[journal.name])
            history, saved = await self._load(connection, portfolio_id)
            if saved != config:
                raise ValueError("Portfolio configuration is immutable")
            return history.state

    async def _load(
        self, connection: AsyncConnection, portfolio_id: UUID
    ) -> tuple[History, dict[str, object]]:
        row = (
            (
                await connection.execute(
                    select(portfolios)
                    .where(portfolios.c.portfolio_id == portfolio_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("Unknown paper portfolio")
        if content_hash(row["config"]) != row["config_hash"]:
            raise ValueError("Portfolio configuration integrity failure")
        history = History(portfolio_id, row["config"])
        recorded = (
            (
                await connection.execute(
                    select(commands)
                    .where(commands.c.portfolio_id == portfolio_id)
                    .order_by(commands.c.sequence)
                    .limit(1001)
                )
            )
            .mappings()
            .all()
        )
        if len(recorded) > 1000:
            raise ValueError("Paper history exceeds bounded scenario capacity")
        try:
            for command in recorded:
                kind = command["kind"]
                if kind not in ("order", "pause", "settle"):
                    raise ValueError("Unknown durable command")
                history.apply(
                    cast(Literal["order", "pause", "settle"], kind),
                    command["key"],
                    command["payload"],
                    command["at"],
                )
            expected = projections(history)
            if (
                row["state"] != history.state.model_dump(mode="json")
                or row["last_event_at"] != history.last_at
            ):
                raise ValueError("Paper state projection differs from command history")
            for table in (orders, journal, events, fills, liquidity, instruments, commands):
                query = select(table)
                if table is fills:
                    query = query.join(orders, fills.c.order_id == orders.c.order_id).where(
                        orders.c.portfolio_id == portfolio_id
                    )
                else:
                    query = query.where(table.c.portfolio_id == portfolio_id)
                actual = (await connection.execute(query.limit(10001))).mappings().all()

                def row_key(
                    value: object,
                    columns: tuple[str, ...] = tuple(column.name for column in table.primary_key),
                ) -> tuple[object, ...]:
                    data = cast(dict[str, object], value)
                    return tuple(data[key] for key in columns)

                actual_map = {row_key(dict(r)): dict(r) for r in actual}
                expected_map = {row_key(r): r for r in expected[table.name]}
                if actual_map != expected_map:
                    raise ValueError("Paper durable projection differs from command history")
            return history, row["config"]
        except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
            raise ValueError("Paper reconciliation failed; execution blocked") from exc

    async def _finish(self, connection: AsyncConnection, history: History, effect: Effect) -> None:
        expected = projections(history)
        command_id = effect.command_id
        for table in (orders, journal, events, fills, commands):
            rows = expected[table.name]
            if table is orders or table is fills:
                rows = [r for r in rows if r["order_id"] == command_id]
            elif table is journal or table is commands:
                rows = [r for r in rows if r["command_id"] == command_id]
            else:
                ids = {
                    uuid5(command_id, "event:" + str(i)) for i in range(1, len(effect.events) + 1)
                }
                rows = [r for r in rows if r["event_id"] in ids]
            if rows:
                await connection.execute(insert(table), rows)
        for table in (liquidity, instruments):
            for value in expected[table.name]:
                statement = insert(table).values(**value)
                updates = {
                    column.name: statement.excluded[column.name]
                    for column in table.columns
                    if not column.primary_key
                }
                await connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=list(table.primary_key), set_=updates
                    )
                )
        await connection.execute(
            portfolios.update()
            .where(portfolios.c.portfolio_id == history.state.portfolio_id)
            .values(state=history.state.model_dump(mode="json"), last_event_at=history.last_at)
        )

    async def _execute(
        self,
        portfolio_id: UUID,
        key: str,
        kind: Literal["order", "pause", "settle"],
        payload: dict[str, object],
        at: datetime,
    ) -> Effect:
        at = clock(at)
        async with self.engine.begin() as connection:
            history, config = await self._load(connection, portfolio_id)
            if "research_run_id" in config:
                raise ValueError("Research portfolio is immutable")
            command_id = uuid5(portfolio_id, kind + ":" + key)
            old = history.effects.get(command_id)
            if old is not None:
                if request_hash(kind, payload) != request_hash(old.kind, old.payload):
                    raise ValueError("Command idempotency key conflict")
                return old
            if len(history.effects) >= 1000:
                raise ValueError("Paper history exceeds bounded scenario capacity")
            effect = history.apply(kind, key, payload, at)
            await self._finish(connection, history, effect)
            return effect

    async def submit(
        self, intent: OrderIntent, quote: ExecutionQuote, now: datetime
    ) -> OrderReceipt:
        # Duplicate detection/result selection occur under the same portfolio lock.
        async with self.engine.begin() as connection:
            history, config = await self._load(connection, intent.portfolio_id)
            if "research_run_id" in config:
                raise ValueError("Research portfolio is immutable")
            command_id = uuid5(intent.portfolio_id, "order:" + intent.idempotency_key)
            old = history.effects.get(command_id)
            if old is not None:
                if request_hash(
                    "order", {"intent": intent.model_dump(mode="json")}
                ) != request_hash(old.kind, old.payload):
                    raise ValueError("Idempotency key conflict")
                assert old.receipt is not None
                return old.receipt.model_copy(update={"duplicate": True})
            if len(history.effects) >= 1000:
                raise ValueError("Paper history exceeds bounded scenario capacity")
            effect = history.apply(
                "order",
                intent.idempotency_key,
                {"intent": intent.model_dump(mode="json"), "quote": quote.model_dump(mode="json")},
                clock(now),
            )
            await self._finish(connection, history, effect)
            assert effect.receipt is not None
            return effect.receipt

    async def pause(
        self, portfolio_id: UUID, key: str, paused: bool, at: datetime
    ) -> PortfolioState:
        return (await self._execute(portfolio_id, key, "pause", {"paused": paused}, at)).state

    async def settle(
        self,
        portfolio_id: UUID,
        key: str,
        instrument: PaperInstrument,
        payout_per_unit: Decimal,
        void: bool,
        at: datetime,
    ) -> PortfolioState:
        payout_per_unit = decimal_amount(payout_per_unit)
        return (
            await self._execute(
                portfolio_id,
                key,
                "settle",
                {
                    "instrument": instrument.model_dump(mode="json"),
                    "payout_per_unit": str(payout_per_unit),
                    "void": void,
                },
                at,
            )
        ).state

    async def get(self, portfolio_id: UUID) -> PortfolioState:
        async with self.engine.begin() as connection:
            history, _ = await self._load(connection, portfolio_id)
            return history.state

    async def reconcile(self, portfolio_id: UUID) -> bool:
        try:
            await self.get(portfolio_id)
            return True
        except (ValueError, ArithmeticError):
            return False
