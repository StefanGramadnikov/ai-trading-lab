"""Kraken observations persist with a stable instrument identity across polls (F-22 guard).

Both polls go through the real adapter with different volatile numbers (prices, volumes, book
sizes). If the adapter ever put a volatile field into the instrument's stored metadata, the market
identity hash would change per poll and the market-row count below would double.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
from ai_trading_lab.market_data.models import Source
from ai_trading_lab.market_data.storage import ObservationStore, markets, snapshots
from tests.contract.test_kraken import KEYS, Synthetic, book, ticker

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def moved_market() -> Synthetic:
    def moved_ticker(_: str, params: dict[str, str]) -> object:
        pairs = params["pair"].split(",")
        return {"error": [], "result": {KEYS[p]: ticker("68123.4") for p in pairs}}

    def moved_book(_: str, params: dict[str, str]) -> object:
        data = book()
        data["bids"][0][1] = "9.999"
        data["asks"][0][1] = "8.888"
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    return Synthetic({"/0/public/Ticker": moved_ticker, "/0/public/Depth": moved_book})


async def count(engine: AsyncEngine, table: object) -> int:
    async with engine.connect() as connection:
        value = await connection.scalar(select(func.count()).select_from(table))  # type: ignore[arg-type]
    return int(value or 0)


@pytest.mark.asyncio
async def test_two_polls_with_different_numbers_reuse_one_market_row_per_pair(
    engine: AsyncEngine,
) -> None:
    pairs = ("XBTUSD", "ETHUSD")
    first = await KrakenAdapter(Synthetic()).collect(pairs)
    second = await KrakenAdapter(moved_market()).collect(pairs)
    assert [o.price for o in first.observations] != [o.price for o in second.observations]
    assert first.observations[0].bids != second.observations[0].bids
    markets_before, snapshots_before = await count(engine, markets), await count(engine, snapshots)
    store = ObservationStore(engine)
    await store.save(Source.KRAKEN, first)
    await store.save(Source.KRAKEN, second)
    assert await count(engine, snapshots) - snapshots_before == 4
    assert await count(engine, markets) - markets_before == 2
