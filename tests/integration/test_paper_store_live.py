"""PaperStore for live-observed portfolios: evidence travels with the order and survives reload."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
from ai_trading_lab.market_data.models import Capture, DataMode
from ai_trading_lab.paper_execution.kraken_evidence import KrakenEntry
from ai_trading_lab.paper_execution.models import OrderIntent, SimulationPolicy
from ai_trading_lab.paper_execution.store import PaperStore
from tests.contract.test_kraken import RECEIVED, Synthetic

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
POLICY = SimulationPolicy.model_validate(
    {
        "version": "v1",
        "fee_bps": "80",
        "latency_ms": 1000,
        "max_age_seconds": 60,
        "future_skew_seconds": 5,
        "max_order_notional": "25",
        "max_position_notional": "25",
        "max_total_exposure": "50",
        "max_cost_bps": "250",
    }
)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def evidence() -> KrakenEntry:
    batch = await KrakenAdapter(Synthetic()).collect(("XBTUSD",))
    got: dict[str, Capture] = {
        c.path.rsplit("/", 1)[-1]: c.model_copy(update={"data_mode": DataMode.OBSERVED})
        for c in batch.captures
    }
    return KrakenEntry(
        pairs_capture=got["AssetPairs"],
        time_capture=got["Time"],
        book_capture=got["Depth"],
        pair="XBTUSD",
        assumption_version="v1",
    )


def markers() -> dict[str, str]:
    return {"live_session_id": str(uuid4()), "live_data_mode": "OBSERVED"}


async def portfolio(store: PaperStore, marks: dict[str, str]):  # type: ignore[no-untyped-def]
    pid = uuid4()
    await store.create(pid, "USD", Decimal("0.00001"), Decimal("100"), POLICY, markers=marks)
    return pid


def order(pid, entry: KrakenEntry, key: str = "buy-1") -> OrderIntent:  # type: ignore[no-untyped-def]
    return OrderIntent.model_validate(
        {
            "portfolio_id": pid,
            "idempotency_key": key,
            "instrument": entry.quote.instrument,
            "side": "buy",
            "quantity": "0.0002",
            "limit_price": "67050.0",
            "created_at_utc": RECEIVED,
            "expires_at_utc": RECEIVED + timedelta(minutes=5),
        }
    )


async def test_observed_order_with_evidence_fills_persists_and_reloads(engine: AsyncEngine) -> None:
    store, marks, item = PaperStore(engine), markers(), await evidence()
    pid = await portfolio(store, marks)
    receipt = await store.submit(order(pid, item), item.quote, RECEIVED, source_entry=item)
    assert receipt.result.status == "FILLED" and receipt.result.fee > 0
    state = await PaperStore(engine).get(pid)  # a fresh store replays and re-verifies the evidence
    assert state.positions and state.positions[0].quantity == Decimal("0.0002")
    assert state.cash < Decimal(100) and state.fees == receipt.result.fee
    again = await store.submit(order(pid, item), item.quote, RECEIVED, source_entry=item)
    assert again.duplicate and (await store.get(pid)) == state
    assert await store.reconcile(pid)


async def test_forged_or_unevidenced_orders_are_refused_and_leave_no_trace(
    engine: AsyncEngine,
) -> None:
    store, item = PaperStore(engine), await evidence()
    pid = await portfolio(store, markers())
    before = await store.get(pid)
    forged = item.quote.model_copy(
        update={"asks": (item.quote.asks[0].model_copy(update={"price": Decimal("60000.0")}),)}
    )
    with pytest.raises(ValueError, match="mismatch"):
        await store.submit(order(pid, item), forged, RECEIVED, source_entry=item)
    with pytest.raises(ValueError, match="evidence"):
        await store.submit(order(pid, item, "buy-2"), item.quote, RECEIVED)
    assert await store.get(pid) == before


async def test_markers_are_part_of_the_immutable_portfolio_configuration(
    engine: AsyncEngine,
) -> None:
    store, marks = PaperStore(engine), markers()
    pid = await portfolio(store, marks)
    await store.create(pid, "USD", Decimal("0.00001"), Decimal("100"), POLICY, markers=marks)
    with pytest.raises(ValueError, match="immutable"):
        await store.create(
            pid, "USD", Decimal("0.00001"), Decimal("100"), POLICY, markers=markers()
        )
