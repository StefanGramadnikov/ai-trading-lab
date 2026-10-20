"""Synthetic PostgreSQL scenarios; no market or financial service calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import ai_trading_lab.paper_execution.cli as cli
from ai_trading_lab.market_data.models import DataMode, DataQuality
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    PaperInstrument,
    SimulationPolicy,
)
from ai_trading_lab.paper_execution.store import (
    PaperStore,
    events,
    fills,
    journal,
    liquidity,
    orders,
    portfolios,
)

pytestmark = pytest.mark.integration
D = Decimal
NOW = datetime(2026, 10, 19, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def asset(prediction: bool = False) -> PaperInstrument:
    return PaperInstrument(
        source="kalshi" if prediction else "solana",
        venue="fixture",
        native_id="yes" if prediction else "token",
        kind="prediction" if prediction else "spot",
        base_unit="contract:YES" if prediction else "token:fixture",
        cash_unit="USD",
        market_id="fixture-event" if prediction else None,
        outcome="YES" if prediction else None,
        tick_size=D("0.01"),
        quantity_step=D("0.01"),
        cash_step=D("0.01"),
    )


def policy() -> SimulationPolicy:
    return SimulationPolicy(
        version="NONOPERATIONAL-fixture-v1",
        fee_bps=D(0),
        latency_ms=0,
        max_age_seconds=60,
        future_skew_seconds=0,
        max_order_notional=D(1000),
        max_position_notional=D(1000),
        max_total_exposure=D(1000),
        max_cost_bps=D(10000),
    )


async def setup(engine: AsyncEngine, cash: str = "100") -> tuple[PaperStore, UUID]:
    store, pid = PaperStore(engine), uuid4()
    await store.create(pid, "USD", D("0.01"), D(cash), policy())
    return store, pid


def intent(
    pid: UUID,
    key: str = "buy",
    *,
    side: str = "buy",
    quantity: str = "1",
    price: str = "10",
    instrument: PaperInstrument | None = None,
    at: datetime = NOW,
) -> OrderIntent:
    return OrderIntent(
        portfolio_id=pid,
        idempotency_key=key,
        instrument=instrument or asset(),
        side=side,
        quantity=D(quantity),
        limit_price=D(price),
        created_at_utc=at,
        expires_at_utc=at + timedelta(seconds=30),
    )


def quote(
    instrument: PaperInstrument | None = None, price: str = "10", capacity: str = "10"
) -> ExecutionQuote:
    instrument = instrument or asset()
    common = dict(
        snapshot_id=uuid4(),
        instrument=instrument,
        event_time_utc=NOW,
        received_at_utc=NOW,
        quality=DataQuality.VALID,
        data_mode=DataMode.FIXTURE,
    )
    if instrument.kind == "prediction":
        return ExecutionQuote(
            **common,
            bids=({"price": "0.49", "quantity": capacity},),
            asks=({"price": price, "quantity": capacity},),
        )
    return ExecutionQuote(**common, spot_price=D(price), spot_capacity=D(capacity), impact_bps=D(0))


@pytest.mark.asyncio
async def test_durable_order_retry_conflict_and_restart(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    request, snapshot = intent(pid), quote()
    receipt = await store.submit(request, snapshot, NOW)
    assert receipt.result.status == "FILLED"
    assert receipt.state.cash == 90
    assert receipt.state.sequence == 4
    restarted = PaperStore(engine)
    duplicate = await restarted.submit(request, snapshot, NOW)
    assert duplicate.duplicate and duplicate.order_id == receipt.order_id
    assert await restarted.reconcile(pid)
    with pytest.raises(ValueError, match="Idempotency"):
        await store.submit(request.model_copy(update={"quantity": D(2)}), snapshot, NOW)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count()).select_from(orders).where(orders.c.portfolio_id == pid)
            )
            == 1
        )
    assert (await restarted.get(pid)).cash == 90


@pytest.mark.asyncio
async def test_concurrent_spending_and_selling_are_serialized(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    snapshot = quote(capacity="100")
    receipts = await asyncio.gather(
        *(store.submit(intent(pid, key, quantity="6"), snapshot, NOW) for key in ("one", "two"))
    )
    assert sorted(r.result.status for r in receipts) == ["FILLED", "REJECTED"]
    assert (await store.get(pid)).cash == 40
    at = NOW + timedelta(seconds=1)
    receipts = await asyncio.gather(
        *(
            store.submit(
                intent(pid, key, side="sell", quantity="6", at=at), quote(capacity="100"), at
            )
            for key in ("sell-one", "sell-two")
        )
    )
    assert sorted(r.result.status for r in receipts) == ["FILLED", "REJECTED"]
    assert not (await store.get(pid)).positions
    assert await store.reconcile(pid)


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_one_fill(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    request, snapshot = intent(pid), quote()
    results = await asyncio.gather(
        store.submit(request, snapshot, NOW), store.submit(request, snapshot, NOW)
    )
    assert sum(r.duplicate for r in results) == 1
    assert results[0].order_id == results[1].order_id
    assert (await store.get(pid)).cash == 90


@pytest.mark.asyncio
async def test_partial_depth_reuse_snapshot_collision_and_definition_change(
    engine: AsyncEngine,
) -> None:
    store, pid = await setup(engine)
    snapshot = quote(capacity="1.5")
    first = await store.submit(intent(pid, quantity="2"), snapshot, NOW)
    assert first.result.status == "PARTIALLY_FILLED" and first.result.quantity == D("1.5")
    assert (await store.submit(intent(pid, "again"), snapshot, NOW)).result.status == "CANCELLED"
    changed = snapshot.model_copy(update={"spot_capacity": D(100)})
    assert (
        await store.submit(intent(pid, "changed"), changed, NOW)
    ).result.reason == "snapshot_conflict"
    instrument = asset().model_copy(update={"tick_size": D("0.1")})
    assert (
        await store.submit(intent(pid, "definition", instrument=instrument), quote(instrument), NOW)
    ).result.reason == "instrument_definition_conflict"
    assert await store.reconcile(pid)


@pytest.mark.asyncio
async def test_atomic_rollback_after_fill_then_retry(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, pid = await setup(engine)
    original = store._finish

    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected database write failure")

    monkeypatch.setattr(store, "_finish", fail)
    request, snapshot = intent(pid), quote()
    with pytest.raises(RuntimeError, match="injected"):
        await store.submit(request, snapshot, NOW)
    assert (await store.get(pid)).cash == 100
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count()).select_from(journal).where(journal.c.portfolio_id == pid)
            )
            == 2
        )
        assert (
            await connection.scalar(
                select(func.count()).select_from(liquidity).where(liquidity.c.portfolio_id == pid)
            )
            == 0
        )
    monkeypatch.setattr(store, "_finish", original)
    assert (await store.submit(request, snapshot, NOW)).result.status == "FILLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payout,void,expected", [("1", False, "100.50"), ("0", False, "99.50"), ("0", True, "100")]
)
async def test_settlement_is_idempotent_and_blocks_reentry(
    engine: AsyncEngine, payout: str, void: bool, expected: str
) -> None:
    store, pid = await setup(engine)
    instrument = asset(True)
    await store.submit(
        intent(pid, instrument=instrument, price="0.50"), quote(instrument, "0.50"), NOW
    )
    state = await store.settle(pid, "resolution", instrument, D(payout), void, NOW)
    assert state.cash == D(expected) and not state.positions
    assert (await store.settle(pid, "resolution", instrument, D(payout), void, NOW)) == state
    with pytest.raises(ValueError, match="idempotency"):
        await store.settle(pid, "resolution", instrument, D("0.25"), void, NOW)
    with pytest.raises(ValueError, match="already settled"):
        await store.settle(pid, "another", instrument, D(payout), void, NOW)
    assert (
        await store.submit(
            intent(pid, "new", instrument=instrument, price="0.50"), quote(instrument, "0.50"), NOW
        )
    ).result.reason == "instrument_settled"
    assert await store.reconcile(pid)


@pytest.mark.asyncio
async def test_pause_expiry_mode_and_currency_rejections(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    state = await store.pause(pid, "halt", True, NOW)
    assert state.paused
    assert await store.pause(pid, "halt", True, NOW) == state
    with pytest.raises(ValueError, match="idempotency"):
        await store.pause(pid, "halt", False, NOW)
    assert (await store.submit(intent(pid), quote(), NOW)).result.reason == "portfolio_paused"
    await store.pause(pid, "resume", False, NOW)
    observed = quote().model_copy(update={"data_mode": DataMode.OBSERVED})
    assert (
        await store.submit(intent(pid, "observed"), observed, NOW)
    ).result.reason == "fixture_mode_required"
    expired = intent(pid, "expired", at=NOW - timedelta(seconds=40))
    assert (await store.submit(expired, quote(), NOW)).result.status == "EXPIRED"
    with pytest.raises(ValueError, match="backwards"):
        await store.pause(pid, "rewind", True, NOW - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_projection_and_order_fill_corruption_fail_closed(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    receipt = await store.submit(intent(pid), quote(), NOW)
    async with engine.begin() as connection:
        await connection.execute(
            fills.update().where(fills.c.order_id == receipt.order_id).values(quantity=D(2))
        )
    assert not await store.reconcile(pid)
    with pytest.raises(ValueError, match="reconciliation"):
        await store.submit(intent(pid, "blocked"), quote(), NOW)
    store, pid = await setup(engine)
    state = await store.get(pid)
    async with engine.begin() as connection:
        await connection.execute(
            portfolios.update()
            .where(portfolios.c.portfolio_id == pid)
            .values(state=state.model_copy(update={"cash": D(99)}).model_dump(mode="json"))
        )
    assert not await store.reconcile(pid)


@pytest.mark.asyncio
async def test_portfolio_isolation_and_create_immutability(engine: AsyncEngine) -> None:
    store, first = await setup(engine)
    _, second = await setup(engine)
    await store.submit(intent(first), quote(), NOW)
    assert (await store.get(second)).cash == 100
    assert (await store.create(first, "USD", D("0.01"), D(100), policy())).cash == 90
    with pytest.raises(ValueError, match="immutable"):
        await store.create(first, "USD", D("0.01"), D(200), policy())
    with pytest.raises(ValueError, match="Unknown"):
        await store.get(uuid4())


@pytest.mark.asyncio
async def test_offline_cli_fixture_persists_and_repeats(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = json.loads(
        await asyncio.to_thread(Path("tests/fixtures/paper_scenario.json").read_text)
    )
    pid = str(uuid4())
    fixture["portfolio_id"] = pid
    fixture["operations"][0]["intent"]["portfolio_id"] = pid
    path = tmp_path / "scenario.json"
    await asyncio.to_thread(path.write_text, json.dumps(fixture))
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    args = cli.parser().parse_args(["run", str(path)])
    assert await cli.run(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["reconciled"]
    assert await cli.run(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["final_state"] == second["final_state"]
    assert await PaperStore(engine).reconcile(UUID(pid))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["orders", "pause", "spot_capacity", "definition", "events", "commands"]
)
async def test_recovery_detects_missing_history_and_corrupt_controls(
    engine: AsyncEngine, damage: str
) -> None:
    from ai_trading_lab.paper_execution.store import commands, instruments

    store, pid = await setup(engine)
    receipt = await store.submit(intent(pid), quote(capacity="1"), NOW)
    await store.pause(pid, "halt", True, NOW)
    state = await store.get(pid)
    async with engine.begin() as connection:
        if damage == "orders":
            await connection.execute(fills.delete().where(fills.c.order_id == receipt.order_id))
            await connection.execute(orders.delete().where(orders.c.order_id == receipt.order_id))
        elif damage == "pause":
            await connection.execute(
                portfolios.update()
                .where(portfolios.c.portfolio_id == pid)
                .values(state=state.model_copy(update={"paused": False}).model_dump(mode="json"))
            )
        elif damage == "spot_capacity":
            await connection.execute(
                liquidity.update().where(liquidity.c.portfolio_id == pid).values(buy=[], sell=[])
            )
        elif damage == "definition":
            await connection.execute(
                instruments.update().where(instruments.c.portfolio_id == pid).values(settled=1)
            )
        elif damage == "events":
            await connection.execute(events.delete().where(events.c.portfolio_id == pid))
        else:
            await connection.execute(
                commands.delete().where(commands.c.command_id == receipt.order_id)
            )
    assert not await store.reconcile(pid)
    with pytest.raises(ValueError, match="reconciliation"):
        await store.submit(intent(pid, "blocked"), quote(), NOW)


@pytest.mark.asyncio
async def test_prediction_liquidity_side_counter_cannot_be_reset(engine: AsyncEngine) -> None:
    store, pid = await setup(engine)
    instrument = asset(True)
    snapshot = quote(instrument, "0.50", "1")
    await store.submit(intent(pid, instrument=instrument, price="0.50"), snapshot, NOW)
    async with engine.begin() as connection:
        await connection.execute(
            liquidity.update().where(liquidity.c.portfolio_id == pid).values(sell=[])
        )
    # Empty sell was originally empty: no false corruption alarm for the unconsumed side.
    assert await store.reconcile(pid)
    async with engine.begin() as connection:
        await connection.execute(
            liquidity.update().where(liquidity.c.portfolio_id == pid).values(buy=["0"])
        )
    assert not await store.reconcile(pid)
