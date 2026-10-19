"""PostgreSQL recovery preserves immutable synthetic replay registration."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.paper_execution.models import OrderIntent
from ai_trading_lab.paper_execution.replay import COLLATERAL_UNIT, ReplayManifest
from ai_trading_lab.paper_execution.store import PaperStore, portfolios
from tests.integration.test_paper_store import policy
from tests.unit.test_paper_replay import NOW, registered

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def replay_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_registered_replay_restart_and_idempotency(replay_engine: AsyncEngine) -> None:
    manifest = registered()
    quote = manifest.quotes[0]
    store, pid = PaperStore(replay_engine), uuid4()
    await store.create(
        pid, COLLATERAL_UNIT, Decimal("0.01"), Decimal("100"), policy(), replay_manifest=manifest
    )
    intent = OrderIntent(
        portfolio_id=pid,
        idempotency_key="registered-buy",
        instrument=quote.instrument,
        side="buy",
        quantity="8",
        limit_price="0.60",
        created_at_utc=NOW,
        expires_at_utc=NOW + timedelta(seconds=1),
    )
    first = await store.submit(intent, quote, now=NOW)
    restarted = PaperStore(replay_engine)
    duplicate = await restarted.submit(intent, quote, now=NOW)
    assert duplicate.duplicate
    assert duplicate.result == first.result
    assert duplicate.result.quantity == Decimal("8")
    await restarted.reconcile(pid)
    await restarted.create(
        pid, COLLATERAL_UNIT, Decimal("0.01"), Decimal("100"), policy(), replay_manifest=manifest
    )
    changed_entry = manifest.entries[0].model_copy(update={"assumption_version": "different"})
    with pytest.raises(ValueError, match="configuration is immutable"):
        await restarted.create(
            pid,
            COLLATERAL_UNIT,
            Decimal("0.01"),
            Decimal("100"),
            policy(),
            replay_manifest=ReplayManifest(entries=(changed_entry,)),
        )
    with pytest.raises(ValueError, match="configuration is immutable"):
        await restarted.create(pid, COLLATERAL_UNIT, Decimal("0.01"), Decimal("100"), policy())
    assert (await restarted.get(pid)).cash == first.state.cash


async def test_default_portfolio_config_stays_backward_compatible(
    replay_engine: AsyncEngine,
) -> None:
    store, pid = PaperStore(replay_engine), uuid4()
    await store.create(pid, "USD", Decimal("0.01"), Decimal("100"), policy())
    async with replay_engine.connect() as connection:
        config = await connection.scalar(
            select(portfolios.c.config).where(portfolios.c.portfolio_id == pid)
        )
    assert isinstance(config, dict)
    assert "replay_manifest" not in config
    await store.reconcile(pid)
