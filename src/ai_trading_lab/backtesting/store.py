"""Atomic, immutable research records and corresponding M3 ledger projections."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.backtesting.reporting import report
from ai_trading_lab.backtesting.runner import evaluate, portfolio_config
from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.paper_execution.store import (
    PaperStore,
    commands,
    events,
    fills,
    instruments,
    journal,
    liquidity,
    orders,
    portfolios,
    projections,
)

strategy_versions = Table(
    "strategy_versions",
    metadata,
    Column("strategy_hash", String(64), primary_key=True),
    Column("strategy_kind", String(200), nullable=False),
    Column("version", String(200), nullable=False),
    Column("config", JSONB, nullable=False),
    UniqueConstraint("strategy_kind", "version"),
)
research_runs = Table(
    "research_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("input_hash", String(64), nullable=False),
    Column(
        "strategy_hash", String(64), ForeignKey("strategy_versions.strategy_hash"), nullable=False
    ),
    Column(
        "portfolio_id",
        UUID(as_uuid=True),
        ForeignKey("paper_portfolios.portfolio_id"),
        nullable=False,
        unique=True,
    ),
    Column("input", JSONB, nullable=False),
    Column("report", JSONB, nullable=False),
    Column("report_hash", String(64), nullable=False),
)
research_decisions = Table(
    "research_decisions",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("research_runs.run_id"), primary_key=True),
    Column("index", Integer, primary_key=True),
    Column("record", JSONB, nullable=False),
)


class ResearchStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def run(self, spec: ResearchInput) -> dict[str, object]:
        evaluation, history = evaluate(spec)
        output = report(spec, evaluation, history)
        config = portfolio_config(spec)
        pid = history.state.portfolio_id
        strategy = spec.strategy.model_dump(mode="json")
        strategy_hash = content_hash(strategy)
        record_rows = [
            dict(run_id=spec.run_id, index=r.index, record=r.model_dump(mode="json"))
            for r in evaluation.decisions
        ]
        async with self.engine.begin() as connection:
            # This insert serializes concurrent identical strategy/run requests;
            # all ledger and research rows commit together, including first funding.
            await connection.execute(
                insert(strategy_versions)
                .values(
                    strategy_hash=strategy_hash,
                    strategy_kind=spec.strategy.kind,
                    version=spec.strategy.version,
                    config=strategy,
                )
                .on_conflict_do_nothing()
            )
            saved_strategy = await connection.scalar(
                select(strategy_versions.c.config).where(
                    strategy_versions.c.strategy_hash == strategy_hash
                )
            )
            if saved_strategy != strategy:
                raise ValueError("Strategy version integrity failure")
            added = await connection.scalar(
                insert(portfolios)
                .values(
                    portfolio_id=pid,
                    config=config,
                    config_hash=content_hash(config),
                    state=history.state.model_dump(mode="json"),
                    last_event_at=history.last_at,
                )
                .on_conflict_do_nothing()
                .returning(portfolios.c.portfolio_id)
            )
            await connection.execute(
                select(portfolios.c.portfolio_id)
                .where(portfolios.c.portfolio_id == pid)
                .with_for_update()
            )
            old = (
                (
                    await connection.execute(
                        select(research_runs).where(research_runs.c.run_id == spec.run_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if old is not None:
                if old["input_hash"] != spec.fingerprint or old["input"] != spec.model_dump(
                    mode="json"
                ):
                    raise ValueError("Research run identity is immutable")
                if (
                    old["report"] != output
                    or old["report_hash"] != content_hash(output)
                    or old["strategy_hash"] != strategy_hash
                    or old["portfolio_id"] != pid
                ):
                    raise ValueError("Research report integrity failure")
                saved_history, saved_config = await PaperStore(self.engine)._load(connection, pid)
                if saved_config != config or projections(saved_history) != projections(history):
                    raise ValueError("Research portfolio configuration conflict")
                actual = (
                    (
                        await connection.execute(
                            select(research_decisions)
                            .where(research_decisions.c.run_id == spec.run_id)
                            .order_by(research_decisions.c.index)
                        )
                    )
                    .mappings()
                    .all()
                )
                if [dict(row) for row in actual] != record_rows:
                    raise ValueError("Research decision integrity failure")
                return cast(dict[str, object], old["report"])
            # A pre-existing orphan portfolio must not be silently overwritten.
            if added is None:
                raise ValueError("Research portfolio identity conflict")
            row = (
                (
                    await connection.execute(
                        select(portfolios).where(portfolios.c.portfolio_id == pid)
                    )
                )
                .mappings()
                .one()
            )
            if (
                row["config"] != config
                or row["config_hash"] != content_hash(config)
                or row["state"] != history.state.model_dump(mode="json")
                or row["last_event_at"] != history.last_at
            ):
                raise ValueError("Research portfolio identity conflict")
            expected = projections(history)
            for table in (orders, journal, events, fills, liquidity, instruments, commands):
                if expected[table.name]:
                    await connection.execute(insert(table), expected[table.name])
            await connection.execute(
                insert(research_runs).values(
                    run_id=spec.run_id,
                    input_hash=spec.fingerprint,
                    strategy_hash=strategy_hash,
                    portfolio_id=pid,
                    input=spec.model_dump(mode="json"),
                    report=output,
                    report_hash=content_hash(output),
                )
            )
            await connection.execute(insert(research_decisions), record_rows)
        return output
