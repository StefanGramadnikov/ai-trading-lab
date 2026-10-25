"""Worker probe uses isolated PostgreSQL and cannot contend with writer advisory locks."""

import asyncio
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.health import probe
from ai_trading_lab.runtime.journal import Journal, events
from tests.unit.test_runtime_engine import config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_probe_is_readonly_current_stale_terminal_and_no_writer_lock(
    database_url: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        raw = config()
        session_id = UUID(raw["session_id"])
        now = datetime.fromisoformat(raw["started_at_utc"])
        journal = Journal(engine, reducer)
        checkpoint = await journal.create(session_id, raw, now)
        # Holding the journal's actual writer advisory lock must not hold up a probe.
        async with engine.begin() as writer:
            await journal._lock(writer, session_id)
            async with asyncio.timeout(2):
                assert await probe(engine, session_id, now=now) == {
                    "healthy": True,
                    "reason": "current",
                    "runtime_status": "RUNNING",
                }
        assert (await probe(engine, session_id, now=now + timedelta(seconds=33)))[
            "reason"
        ] == "stale"
        assert await journal.get(session_id) == checkpoint
        async with engine.connect() as reader:
            assert (
                await reader.scalar(
                    select(func.count())
                    .select_from(events)
                    .where(events.c.session_id == session_id)
                )
                == 0
            )
        await journal.append(session_id, "stop", {"action": "stop"}, now)
        assert (await probe(engine, session_id, now=now))["reason"] == "terminal"
    finally:
        await engine.dispose()
