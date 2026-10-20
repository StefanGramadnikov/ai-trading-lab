"""Bounded experiment lifecycle with atomic cells and verified command replay."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID as UUIDValue
from uuid import uuid5

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore, budgets
from ai_trading_lab.ai.run_store import projection_hash
from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.backtesting.runner import portfolio_config
from ai_trading_lab.experiments.models import ExperimentConfig, State, transition
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
from ai_trading_lab.run_identity import claim_run, run_owners

experiments = Table(
    "experiments",
    metadata,
    Column("experiment_id", UUID(as_uuid=True), primary_key=True),
    Column("config", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("engine_hash", String(64), nullable=False),
    Column("checkpoint", JSONB, nullable=False),
    Column("checkpoint_hash", String(64), nullable=False),
)
experiment_events = Table(
    "experiment_events",
    metadata,
    Column(
        "experiment_id",
        UUID(as_uuid=True),
        ForeignKey("experiments.experiment_id"),
        primary_key=True,
    ),
    Column("sequence", Integer, primary_key=True),
    Column("key", String(200), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("event_hash", String(64), nullable=False),
    UniqueConstraint("experiment_id", "key", name="uq_experiment_event_key"),
)


def _payload(key: str, action: str, at: datetime, reason: str = "") -> dict[str, object]:
    if not key or len(key) > 200 or len(reason) > 1000:
        raise ValueError("bounded command key and reason required")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return {"action": action, "at": at.astimezone(UTC).isoformat(), "reason": reason}


def _snapshot(eid: UUIDValue, checkpoint: dict[str, Any], total: int) -> dict[str, object]:
    return {
        "experiment_id": str(eid),
        "state": checkpoint["state"],
        "completed_cells": len(checkpoint["reports"]),
        "total_cells": total,
        "last_sequence": checkpoint["sequence"],
        "resume_state": checkpoint["resume_state"],
        "reports_hash": content_hash(checkpoint["reports"]),
    }


class ExperimentStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def _lock(self, connection: AsyncConnection, eid: UUIDValue) -> None:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(eid.hex[:16], 16) - (1 << 63)}
        )

    async def create(self, config: ExperimentConfig, key: str, at: datetime) -> dict[str, object]:
        config = ExperimentConfig.model_validate(config.model_dump(mode="json"))
        payload = _payload(key, "create", at)
        eid = config.experiment_id
        async with asyncio.timeout(300), self.engine.begin() as connection:
            await self._lock(connection, eid)
            exists = await connection.scalar(
                select(experiments.c.experiment_id).where(experiments.c.experiment_id == eid)
            )
            if exists is not None:
                saved, _, records = await self._load(connection, eid)
                if saved != config:
                    raise ValueError("experiment configuration is immutable")
                replay = self._replay(records, key, payload)
                if replay is None:
                    raise ValueError("experiment already exists")
                return replay
            for cell in sorted(config.cells, key=lambda item: str(item.research.run_id)):
                await claim_run(
                    connection,
                    cell.research.run_id,
                    "experiment",
                    eid,
                    content_hash(cell.model_dump(mode="json")),
                )
            checkpoint: dict[str, Any] = {
                "state": "CREATED",
                "resume_state": None,
                "sequence": 0,
                "reports": [],
                "event_hash": "0" * 64,
                "at": payload["at"],
            }
            await connection.execute(
                insert(experiments).values(
                    experiment_id=eid,
                    config=config.model_dump(mode="json"),
                    config_hash=config.fingerprint,
                    engine_hash=runtime_fingerprint(),
                    checkpoint=checkpoint,
                    checkpoint_hash=content_hash(checkpoint),
                )
            )
            return await self._append(connection, config, checkpoint, key, payload)

    async def _load(
        self, connection: AsyncConnection, eid: UUIDValue, *, for_update: bool = True
    ) -> tuple[ExperimentConfig, dict[str, Any], list[Any]]:
        row = (
            (
                await connection.execute(
                    select(experiments).where(experiments.c.experiment_id == eid)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("unknown experiment")
        config = ExperimentConfig.model_validate(row["config"])
        if config.fingerprint != row["config_hash"] or row["engine_hash"] != runtime_fingerprint():
            raise ValueError("experiment configuration or runtime integrity failure")
        for cell in config.cells:
            owner = (
                (
                    await connection.execute(
                        select(run_owners).where(run_owners.c.run_id == cell.research.run_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if owner is None or (
                owner["owner_kind"] != "experiment"
                or owner["owner_id"] != eid
                or owner["config_hash"] != content_hash(cell.model_dump(mode="json"))
            ):
                raise ValueError("experiment run ownership integrity failure")
        checkpoint = cast(dict[str, Any], row["checkpoint"])
        if (
            not isinstance(checkpoint, dict)
            or set(checkpoint)
            != {"state", "resume_state", "sequence", "reports", "event_hash", "at"}
            or not isinstance(checkpoint["reports"], list)
            or len(checkpoint["reports"]) > len(config.cells)
        ):
            raise ValueError("experiment checkpoint integrity failure")
        records = list(
            (
                await connection.execute(
                    select(experiment_events)
                    .where(experiment_events.c.experiment_id == eid)
                    .order_by(experiment_events.c.sequence)
                    .limit(1001)
                )
            )
            .mappings()
            .all()
        )
        previous = "0" * 64
        for sequence, record in enumerate(records, 1):
            expected = content_hash(
                {
                    "sequence": sequence,
                    "key": record["key"],
                    "payload": record["payload"],
                    "snapshot": record["snapshot"],
                    "previous_hash": previous,
                }
            )
            if (
                record["sequence"] != sequence
                or record["previous_hash"] != previous
                or record["event_hash"] != expected
            ):
                raise ValueError("experiment event integrity failure")
            previous = expected
        if (
            not records
            or len(records) > 1000
            or checkpoint["sequence"] != len(records)
            or checkpoint["event_hash"] != previous
            or row["checkpoint_hash"] != content_hash(checkpoint)
            or records[-1]["snapshot"] != _snapshot(eid, checkpoint, len(config.cells))
        ):
            raise ValueError("experiment checkpoint integrity failure")
        # All committed paper projections and inference costs are authoritative on replay.
        from ai_trading_lab.experiments.runner import verify_cell_costs

        for cell in config.cells:
            if (
                cell.ai is not None
                and await connection.scalar(
                    select(budgets.c.budget_id).where(
                        budgets.c.budget_id == cell.ai.budget.budget_id
                    )
                )
                is not None
            ):
                await BudgetStore(self.engine).summary(
                    cell.ai.budget.budget_id, snapshot=connection if not for_update else None
                )
        for index, saved in enumerate(checkpoint["reports"]):
            cell = config.cells[index]
            history, actual_config = await PaperStore(self.engine)._load(
                connection, uuid5(eid, "cell:" + cell.cell_id), for_update=for_update
            )
            if actual_config != portfolio_config(
                cell.profile.effective(cell.research)
            ) or projection_hash(history) != saved.get("paper_projection_hash"):
                raise ValueError("experiment paper projection integrity failure")
            await verify_cell_costs(
                self.engine, config, cell, saved, snapshot=connection if not for_update else None
            )
        return config, checkpoint, records

    @staticmethod
    def _replay(
        records: list[Any], key: str, payload: dict[str, object]
    ) -> dict[str, object] | None:
        for record in records:
            if record["key"] == key:
                if record["payload"] != payload:
                    raise ValueError("idempotency key payload conflict")
                return cast(dict[str, object], record["snapshot"])
        return None

    async def _append(
        self,
        connection: AsyncConnection,
        config: ExperimentConfig,
        checkpoint: dict[str, Any],
        key: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if checkpoint["sequence"] >= 1000:
            raise ValueError("experiment command capacity exceeded")
        if datetime.fromisoformat(str(payload["at"])) < datetime.fromisoformat(checkpoint["at"]):
            raise ValueError("experiment timestamp regression")
        checkpoint["sequence"] += 1
        checkpoint["at"] = payload["at"]
        snapshot = _snapshot(config.experiment_id, checkpoint, len(config.cells))
        event = {
            "sequence": checkpoint["sequence"],
            "key": key,
            "payload": payload,
            "snapshot": snapshot,
            "previous_hash": checkpoint["event_hash"],
        }
        event_hash = content_hash(event)
        await connection.execute(
            insert(experiment_events).values(
                experiment_id=config.experiment_id, **event, event_hash=event_hash
            )
        )
        checkpoint["event_hash"] = event_hash
        await connection.execute(
            update(experiments)
            .where(experiments.c.experiment_id == config.experiment_id)
            .values(checkpoint=checkpoint, checkpoint_hash=content_hash(checkpoint))
        )
        return snapshot

    async def command(
        self, experiment_id: UUIDValue, key: str, action: str, at: datetime, reason: str
    ) -> dict[str, object]:
        if action not in {"start", "pause", "resume", "approve", "reject"}:
            raise ValueError("unsupported experiment command")
        payload = _payload(key, action, at, reason)
        async with asyncio.timeout(300), self.engine.begin() as connection:
            await self._lock(connection, experiment_id)
            config, checkpoint, records = await self._load(connection, experiment_id)
            replay = self._replay(records, key, payload)
            if replay is not None:
                return replay
            state = cast(State, checkpoint["state"])
            next_state = transition(state, action, checkpoint["resume_state"])
            if action == "approve":
                from ai_trading_lab.experiments.reporting import build_report

                if not build_report(config, tuple(checkpoint["reports"]))["evidence_sufficient"]:
                    raise ValueError("insufficient experiment evidence for approval")
            checkpoint["resume_state"] = state if action == "pause" else None
            checkpoint["state"] = next_state
            return await self._append(connection, config, checkpoint, key, payload)

    async def advance(self, experiment_id: UUIDValue, key: str, at: datetime) -> dict[str, object]:
        payload = _payload(key, "advance", at)
        async with asyncio.timeout(300), self.engine.begin() as connection:
            await self._lock(connection, experiment_id)
            config, checkpoint, records = await self._load(connection, experiment_id)
            replay = self._replay(records, key, payload)
            if replay is not None:
                return replay
            if checkpoint["state"] != "RUNNING":
                raise ValueError("advance requires RUNNING experiment")
            if datetime.fromisoformat(str(payload["at"])) < datetime.fromisoformat(
                checkpoint["at"]
            ):
                raise ValueError("experiment timestamp regression")
            from ai_trading_lab.experiments.runner import evaluate_cell

            cell = config.cells[len(checkpoint["reports"])]
            evaluated = await evaluate_cell(config, cell, self.engine)
            history = evaluated.history
            config_payload = portfolio_config(evaluated.research)
            await connection.execute(
                insert(portfolios).values(
                    portfolio_id=history.state.portfolio_id,
                    config=config_payload,
                    config_hash=content_hash(config_payload),
                    state=history.state.model_dump(mode="json"),
                    last_event_at=history.last_at,
                )
            )
            projected = projections(history)
            for table in (orders, journal, events, fills, liquidity, instruments, commands):
                if projected[table.name]:
                    await connection.execute(insert(table), projected[table.name])
            saved = dict(evaluated.report)
            saved["paper_projection_hash"] = projection_hash(history)
            checkpoint["reports"].append(saved)
            finished = len(checkpoint["reports"]) == len(config.cells)
            next_state = "EVALUATING" if finished else "RUNNING"
            checkpoint["state"] = "PAUSED" if saved.get("entry_suspended") else next_state
            checkpoint["resume_state"] = next_state if saved.get("entry_suspended") else None
            return await self._append(connection, config, checkpoint, key, payload)

    async def report(self, experiment_id: UUIDValue) -> dict[str, object]:
        from ai_trading_lab.experiments.reporting import build_report

        async with asyncio.timeout(300), self.engine.begin() as connection:
            await self._lock(connection, experiment_id)
            config, checkpoint, _ = await self._load(connection, experiment_id)
            output = build_report(config, tuple(checkpoint["reports"]))
            output["lifecycle"] = _snapshot(experiment_id, checkpoint, len(config.cells))
            return output
