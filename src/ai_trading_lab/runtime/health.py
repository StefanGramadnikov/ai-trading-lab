"""Read-only, bounded worker progress diagnostics, separate from API/DB liveness."""

import argparse
import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.runtime.journal import sessions


def evaluate_progress(row: Mapping[str, object] | None, now: datetime) -> dict[str, object]:
    if row is None:
        return {"healthy": False, "reason": "session_missing"}
    try:
        config, checkpoint = row["config"], row["checkpoint"]
        if not isinstance(config, dict) or not isinstance(checkpoint, dict):
            raise ValueError("invalid_progress")
        state = checkpoint["state"]
        if not isinstance(state, dict):
            raise ValueError("invalid_progress")
        interval = config["poll_interval_seconds"]
        last = datetime.fromisoformat(checkpoint["last_at"])
        deadline = datetime.fromisoformat(config["deadline_at_utc"])
        status = state["status"]
        if (
            type(interval) is not int
            or not 1 <= interval <= 3600
            or status not in ("RUNNING", "PAUSED", "DEGRADED", "STOPPED")
            or any(
                stamp.tzinfo is None or stamp.utcoffset() is None for stamp in (now, last, deadline)
            )
            or last > now
        ):
            raise ValueError("invalid_progress")
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"healthy": False, "reason": "invalid_progress"}
    if status == "STOPPED" or now >= deadline:
        return {"healthy": False, "reason": "terminal", "runtime_status": status}
    # Allow one missed cadence and bounded collection/persistence overhead. This is
    # progress age only: fresh DEGRADED/PAUSED checkpoints never clear suspension.
    current = (now - last).total_seconds() <= 2 * interval + 30
    return {
        "healthy": current,
        "reason": "current" if current else "stale",
        "runtime_status": status,
    }


async def probe(
    engine: AsyncEngine, session_id: UUID, *, now: datetime | None = None
) -> dict[str, object]:
    try:
        async with asyncio.timeout(3), engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(
                select(sessions.c.config, sessions.c.checkpoint).where(
                    sessions.c.session_id == session_id
                )
            )
            row = result.mappings().one_or_none()
        return evaluate_progress(
            None if row is None else dict(row),
            now if now is not None else datetime.now(UTC),
        )
    except (SQLAlchemyError, TimeoutError, OSError):
        return {"healthy": False, "reason": "persistence_unavailable"}


async def run(session_id: UUID) -> dict[str, object]:
    engine = create_database_engine(Settings())
    try:
        return await probe(engine, session_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only runtime progress probe")
    parser.add_argument("session_id", type=UUID)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.session_id))
    except (ValueError, SQLAlchemyError, TimeoutError, OSError):
        result = {"healthy": False, "reason": "probe_unavailable"}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["healthy"] else 1)


if __name__ == "__main__":
    main()
