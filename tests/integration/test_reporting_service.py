"""Actual PostgreSQL reads, identity integrity and exports without state mutation."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.experiments.store import ExperimentStore, experiments
from ai_trading_lab.reporting.service import ReportingService
from tests.experiment_helpers import experiment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
AT = datetime(2026, 10, 20, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def test_verified_detail_export_matches_m6_without_writes(engine: AsyncEngine) -> None:
    config = experiment()
    store = ExperimentStore(engine)
    await store.create(config, "create", AT)
    await store.command(config.experiment_id, "start", "start", AT, "")
    for cell in config.cells:
        await store.advance(config.experiment_id, cell.cell_id, AT)
    before = await store.report(config.experiment_id)
    service = ReportingService(engine)
    detail = await service.detail(config.experiment_id)
    assert detail["cells"][0]["economic_net_pnl"] == before["cells"][0]["economic_net_pnl"]  # type: ignore[index]
    assert "configuration" not in detail
    exported = json.loads(await service.export(config.experiment_id))
    assert exported["report"]["config_hash"] == config.fingerprint
    assert exported["ledger"]["paper_fills"]
    assert "fixture_results" not in json.dumps(exported)
    assert "ai_traces" not in json.dumps(exported)
    assert await service.export(config.experiment_id) == await service.export(config.experiment_id)
    assert await store.report(config.experiment_id) == before


async def test_list_pagination_historical_refusal_and_corruption(engine: AsyncEngine) -> None:
    config = experiment()
    await ExperimentStore(engine).create(config, "create", AT)
    service = ReportingService(engine)
    page = await service.list_experiments(100, 0)
    assert any(
        item["experiment_id"] == str(config.experiment_id)
        for item in cast(list[dict[str, Any]], page["items"])
    )
    assert (await service.status())["trading_ready"] is False
    with pytest.raises(LookupError):
        await service.detail(uuid4())
    with pytest.raises(ValueError):
        await service.list_experiments(101, 0)
    async with engine.begin() as connection:
        await connection.execute(
            update(experiments)
            .where(experiments.c.experiment_id == config.experiment_id)
            .values(engine_hash="0" * 64)
        )
    with pytest.raises(ValueError, match="integrity"):
        await service.detail(config.experiment_id)
    with pytest.raises(ValueError):
        await service.export(config.experiment_id)
    async with engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    select(experiments).where(experiments.c.experiment_id == config.experiment_id)
                )
            )
            .mappings()
            .one()
        )
        checkpoint = dict(row["checkpoint"])
        checkpoint["state"] = "APPROVED"
        await connection.execute(
            update(experiments)
            .where(experiments.c.experiment_id == config.experiment_id)
            .values(engine_hash=runtime_fingerprint(), checkpoint=checkpoint)
        )
    with pytest.raises(ValueError):
        await service.detail(config.experiment_id)


async def test_repeated_notification_scans_and_budget_snapshot_do_not_lock_writers(
    engine: AsyncEngine,
) -> None:
    import asyncio
    from datetime import timedelta
    from decimal import Decimal

    from ai_trading_lab.ai.budget import budgets
    from ai_trading_lab.notifications.store import NotificationStore
    from ai_trading_lab.reporting.cli import prepare

    config = experiment()
    store = ExperimentStore(engine)
    await store.create(config, "create", AT)
    await store.command(config.experiment_id, "start", "start", AT, "")
    for cell in config.cells:
        await store.advance(config.experiment_id, cell.cell_id, AT)
    ai = config.cells[1].ai
    assert ai is not None
    async with engine.begin() as writer:
        await writer.execute(
            select(budgets).where(budgets.c.budget_id == ai.budget.budget_id).with_for_update()
        )
        async with asyncio.timeout(2):
            report = await ReportingService(engine).detail(config.experiment_id)
    queue = NotificationStore(engine)
    first = prepare(cast(dict[str, Any], report), AT, Decimal("0.8"))
    for item in first:
        saved = await queue.enqueue(item, AT)
        assert saved["state"] == "pending"
    later = AT + timedelta(hours=1)
    second = prepare(cast(dict[str, Any], report), later, Decimal("0.8"))
    assert first == second
    for item in second:
        saved_again = await queue.enqueue(item, later)
        assert saved_again["received_at_utc"] == AT
        assert saved_again["state"] == "pending"
