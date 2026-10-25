"""Bounded read-only views; M6 replay remains the authority for financial results."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.experiments.reporting import build_report
from ai_trading_lab.experiments.store import ExperimentStore, experiments
from ai_trading_lab.market_data.storage import content_hash, raw_events, snapshots
from ai_trading_lab.paper_execution.store import events, fills, journal, orders
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.journal import Journal, sessions

MAX_EXPORT_BYTES = 8_000_000


class Reports(Protocol):
    async def list_experiments(self, limit: int, offset: int) -> dict[str, object]: ...
    async def detail(self, experiment_id: UUID) -> dict[str, object]: ...
    async def export(self, experiment_id: UUID) -> bytes: ...
    async def status(self) -> dict[str, object]: ...
    async def runtime_sessions(self, limit: int = 20) -> dict[str, object]: ...
    async def runtime_detail(self, session_id: UUID) -> dict[str, object]: ...


class ReportingService:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        # One journal keeps its verified-continuation cache across dashboard requests.
        self._journal = Journal(engine, reducer)

    async def list_experiments(self, limit: int = 20, offset: int = 0) -> dict[str, object]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("invalid pagination")
        async with asyncio.timeout(10), self.engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                await connection.execute(
                    select(
                        experiments.c.experiment_id,
                        experiments.c.config_hash,
                        experiments.c.engine_hash,
                        experiments.c.checkpoint["state"].astext.label("state"),
                        func.jsonb_array_length(experiments.c.config["cells"]).label("total"),
                        func.jsonb_array_length(experiments.c.checkpoint["reports"]).label(
                            "completed"
                        ),
                        experiments.c.checkpoint["at"].astext.label("at"),
                    )
                    .order_by(experiments.c.experiment_id)
                    .limit(limit)
                    .offset(offset)
                )
            ).mappings()
            current = runtime_fingerprint()
            items = [
                {
                    "experiment_id": str(row["experiment_id"]),
                    "config_hash": row["config_hash"],
                    "engine_hash": row["engine_hash"],
                    "runtime_compatible": row["engine_hash"] == current,
                    "state": row["state"],
                    "completed_cells": row["completed"],
                    "total_cells": row["total"],
                    "as_of_utc": row["at"],
                    "verification": "unverified_index_metadata",
                }
                for row in rows
            ]
        return {"items": items, "limit": limit, "offset": offset}

    async def _view(self, experiment_id: UUID, *, audit: bool) -> dict[str, object]:
        async with asyncio.timeout(15), self.engine.begin() as connection:
            # One immutable snapshot also prevents a mixed report/export during cell commit.
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            if (
                await connection.scalar(
                    select(experiments.c.experiment_id).where(
                        experiments.c.experiment_id == experiment_id
                    )
                )
                is None
            ):
                raise LookupError("unknown experiment")
            config, checkpoint, records = await ExperimentStore(self.engine)._load(
                connection, experiment_id, for_update=False
            )
            report = build_report(config, tuple(checkpoint["reports"]))
            report["lifecycle"] = {
                "experiment_id": str(experiment_id),
                "state": checkpoint["state"],
                "completed_cells": len(checkpoint["reports"]),
                "total_cells": len(config.cells),
                "last_sequence": checkpoint["sequence"],
                "resume_state": checkpoint["resume_state"],
                "reports_hash": content_hash(checkpoint["reports"]),
                "as_of_utc": checkpoint["at"],
            }
            for cell_view, raw in zip(
                cast(list[dict[str, object]], report["cells"]), checkpoint["reports"], strict=False
            ):
                cell_view["suspension_reason"] = raw.get("suspension_reason")
                cell_view["inference_budget"] = raw.get("inference_budget")
            if not audit:
                # Presentation needs provenance and metrics, never full AI input/output payloads.
                report.pop("configuration", None)
                for cell in cast(list[dict[str, object]], report["cells"]):
                    cell.pop("ai_traces", None)
                return report
            run_ids = [uuid5(experiment_id, "cell:" + cell.cell_id) for cell in config.cells]
            ledger: dict[str, object] = {}
            for table in (orders, fills, journal, events):
                statement = select(table)
                if table is fills:
                    statement = statement.join(orders, fills.c.order_id == orders.c.order_id)
                    predicate = orders.c.portfolio_id.in_(run_ids)
                else:
                    predicate = table.c.portfolio_id.in_(run_ids)
                rows = list(
                    (
                        await connection.execute(
                            statement.where(predicate)
                            .order_by(*table.primary_key.columns)
                            .limit(16001)
                        )
                    ).mappings()
                )
                if len(rows) > 16000:
                    raise ValueError("audit row limit exceeded")
                ledger[table.name] = [dict(row) for row in rows]
            # Configuration hashes and structured settings survive redaction. Raw fixtures,
            # market text and model traces can contain untrusted/private prompt content.
            report.pop("configuration", None)
            for cell in cast(list[dict[str, object]], report["cells"]):
                cell.pop("ai_traces", None)
            configuration = config.model_dump(mode="json")
            for cell_config in configuration["cells"]:
                if cell_config["ai"]:
                    cell_config["ai"].pop("fixture_results", None)
                    cell_config["ai"].pop("fixture_responses", None)
            return {
                "schema_version": "1",
                "execution_mode": "PAPER_ONLY",
                "report": report,
                "configuration": configuration,
                "lifecycle_events": [dict(record) for record in records],
                "ledger": ledger,
                "export_basis": "verified_current_runtime_snapshot",
                "limitations": [
                    "Redacted presentation; original configuration hash "
                    "is not the hash of redacted JSON",
                    "Database-owner tamper resistance is not established",
                ],
            }

    async def detail(self, experiment_id: UUID) -> dict[str, object]:
        return await self._view(experiment_id, audit=False)

    async def export(self, experiment_id: UUID) -> bytes:
        value = await self._view(experiment_id, audit=True)
        result = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
        if len(result) > MAX_EXPORT_BYTES:
            raise ValueError("export exceeds 8MB")
        return result

    async def runtime_sessions(self, limit: int = 20) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid runtime page size")
        async with asyncio.timeout(5), self.engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                await connection.execute(
                    select(
                        sessions.c.session_id,
                        sessions.c.config_hash,
                        sessions.c.engine_hash,
                        sessions.c.checkpoint["last_at"].astext.label("last_at"),
                        sessions.c.checkpoint["sequence"].astext.label("sequence"),
                        sessions.c.checkpoint["state"]["status"].astext.label("status"),
                    )
                    .order_by(sessions.c.session_id)
                    .limit(limit)
                )
            ).mappings()
            current = runtime_fingerprint()
            result = [
                {
                    "session_id": str(row["session_id"]),
                    "runtime_compatible": row["engine_hash"] == current,
                    "last_at": row["last_at"],
                    "sequence": row["sequence"],
                    "status": row["status"],
                    "verification": "unverified_index_metadata",
                }
                for row in rows
            ]
        return {
            "items": result,
            "limit": limit,
            "heartbeat_basis": "last durable progress; not a live process probe",
        }

    async def runtime_detail(self, session_id: UUID) -> dict[str, object]:
        checkpoint = await self._journal.get(session_id)
        state = cast(dict[str, object], checkpoint["state"])
        # Full source evidence remains in the local journal, not browser diagnostics.
        visible = {
            key: state.get(key)
            for key in (
                "status",
                "portfolio",
                "nav",
                "entry_suspended",
                "suspension_reason",
                "cycles",
                "poll_attempts",
                "poll_pending",
                "last_at",
                "peak_nav",
                "day",
                "day_open_realized_net",
                "risk_latch",
                "history_hash",
                "paper_only",
                "ai_enabled",
            )
        }
        return {
            **checkpoint,
            "state": visible,
            "heartbeat_basis": "last durable progress; not a live process probe",
            # The runtime has no settlement or discretionary exit path (CODEX_FIXES F-12), so
            # NAV is a full-liquidation mark. It is never a realized or final result.
            "economic_basis": "mark_only_no_realization",
            "final_pnl_claim": False,
        }

    async def status(self) -> dict[str, object]:
        async with asyncio.timeout(5), self.engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                (
                    await connection.execute(
                        select(
                            snapshots.c.received_at_utc,
                            snapshots.c.event_time_utc,
                            snapshots.c.quality,
                            raw_events.c.source,
                            raw_events.c.data_mode,
                        )
                        .join(raw_events, snapshots.c.capture_id == raw_events.c.capture_id)
                        .order_by(snapshots.c.received_at_utc.desc(), snapshots.c.snapshot_id)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        observation: dict[str, Any] = (
            dict(row)
            if row
            else {
                "received_at_utc": None,
                "event_time_utc": None,
                "quality": None,
                "source": None,
                "data_mode": None,
            }
        )
        for key in ("received_at_utc", "event_time_utc"):
            if isinstance(observation[key], datetime):
                observation[key] = observation[key].isoformat()
        return {
            "execution_mode": "PAPER_ONLY",
            "trading_ready": False,
            "database": "available",
            "observation": observation,
            "as_of_utc": datetime.now(UTC).isoformat(),
            "freshness_basis": "last persisted observation; not a live source-health probe",
        }
