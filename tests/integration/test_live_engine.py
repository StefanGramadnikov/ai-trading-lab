"""The live cycle end to end on PostgreSQL, with a fake Kraken that shifts time and moves prices."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.engine import LiveEngine
from ai_trading_lab.live.errors import LoopIntegrityError
from ai_trading_lab.live.risk import RiskState
from ai_trading_lab.live.store import LiveStore, LoopState, live_candles
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import Capture, DataMode, Source, TransportError
from ai_trading_lab.paper_execution.store import PaperStore
from tests.contract.test_kraken import KEYS, RECEIVED, SERVER, Synthetic, candles

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
D = Decimal
PAIRS = ("XBTUSD", "ETHUSD", "SOLUSD")
CONFIG = LiveConfig(pairs=PAIRS, cash_unit="USD")
START = datetime.fromtimestamp(SERVER, UTC)


class Venue(Synthetic):
    """Synthetic Kraken that returns OBSERVED captures, at a shifted time and scaled prices."""

    def __init__(self) -> None:
        super().__init__()
        self.shift = 0
        self.scale = D(1)
        self.rows: list[list[Any]] | None = None
        self.time_down = False

    def default(self, path: str, params: dict[str, str]) -> object:
        reply: Any = super().default(path, params)
        result = reply["result"]
        if path == "/0/public/Time":
            result["unixtime"] = SERVER + self.shift
        elif path == "/0/public/Depth":
            for side in result.values():
                for levels in (side["asks"], side["bids"]):
                    for level in levels:
                        price = (D(level[0]) * self.scale).quantize(D("0.1"))
                        level[0], level[2] = str(price), level[2] + self.shift
        elif path == "/0/public/OHLC":
            key = KEYS[params["pair"]]
            rows = self.rows if self.rows is not None else candles()
            result[key] = [[row[0] + self.shift, *row[1:]] for row in rows]
        return reply

    async def request(
        self, source: Source, method: Literal["GET", "POST"], path: str, **kwargs: Any
    ) -> Capture:
        if self.time_down and path == "/0/public/Time":
            raise TransportError(Source.KRAKEN, "network_error")
        capture = await super().request(source, method, path, **kwargs)
        return capture.model_copy(
            update={
                "data_mode": DataMode.OBSERVED,
                "received_at_utc": RECEIVED + timedelta(seconds=self.shift),
            }
        )


def breakout_row(open_offset: int = -900) -> list[Any]:
    return [SERVER + open_offset, "69000.0", "70000.0", "68000.0", "69500.0", "69200.0", "25", 40]


def flat_history(count: int, end_open_offset: int = -1800) -> list[Candle]:
    """`count` contiguous flat 15-minute candles on a 100-scale, the last at SERVER+offset."""
    rows = []
    for index in range(count):
        opened = (
            START + timedelta(seconds=end_open_offset) - timedelta(minutes=15 * (count - 1 - index))
        )
        rows.append(
            Candle(
                open_time_utc=opened,
                close_time_utc=opened + timedelta(minutes=15),
                interval_minutes=15,
                open=D(100),
                high=D(101),
                low=D(99),
                close=D(100),
                vwap=D(100),
                volume=D(10),
                trades=5,
            )
        )
    return rows


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    async with value.begin() as connection:
        await connection.execute(delete(live_candles))
    try:
        yield value
    finally:
        await value.dispose()


class Rig:
    def __init__(self, engine: AsyncEngine, venue: Venue, config: LiveConfig = CONFIG) -> None:
        self.engine, self.venue, self.config = engine, venue, config
        self.session = f"t{uuid4().hex[:8]}"
        self.now = RECEIVED + timedelta(seconds=1)
        self.store = LiveStore(engine)
        self.paper = PaperStore(engine)

        @asynccontextmanager
        async def factory() -> AsyncIterator[Venue]:
            yield self.venue

        self.live = LiveEngine(
            engine, factory, config, clock=lambda: self.now, session=self.session
        )

    @property
    def rules_id(self) -> str:
        return f"{self.session}-rules-breakout"

    @property
    def bench_id(self) -> str:
        return f"{self.session}-benchmark"

    async def cycle(self, minutes_later: int = 0):  # type: ignore[no-untyped-def]
        if minutes_later:
            self.now += timedelta(minutes=minutes_later)
            self.venue.shift += minutes_later * 60
        return await self.live.run_cycle()

    async def portfolio(self, strategy_id: str):  # type: ignore[no-untyped-def]
        row = next(
            r
            for r in await self.store.strategies(enabled_only=False)
            if r.strategy_id == strategy_id
        )
        return await self.paper.get(row.portfolio_id)


async def rig(engine: AsyncEngine, venue: Venue | None = None) -> Rig:
    item = Rig(engine, venue or Venue())
    await item.live.setup()
    return item


async def test_the_benchmark_buys_each_pair_once_and_the_rules_strategy_waits_for_history(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    report = await r.cycle()
    assert report.failures == []
    bench = await r.portfolio(r.bench_id)
    assert len(bench.positions) == 3 and bench.fees > 0
    spent = sum(p.cost_basis for p in bench.positions)
    assert (
        D("97") < spent + bench.fees <= D(100)
    )  # a third each, fee included, rounding to the step
    decisions = await r.store.decisions(r.bench_id)
    assert [(d.pair, d.action, d.status) for d in decisions] == [
        (p, "buy", "filled") for p in PAIRS
    ]
    rules = await r.store.decisions(r.rules_id)
    assert {d.reason for d in rules} == {"insufficient_history"} and len(rules) == 3
    assert (await r.portfolio(r.rules_id)).positions == ()
    # Second cycle: nothing more to buy; the benchmark never trades again.
    again = await r.cycle(minutes_later=15)
    assert again.strategies[r.bench_id].orders == 0
    assert len((await r.portfolio(r.bench_id)).positions) == 3
    curve = await r.store.equity_curve(r.bench_id)
    assert len(curve) == 2 and curve[0].equity < D(100)  # fees and spread are real


async def seed_breakout(r: Rig) -> None:
    await r.store.upsert_candles("XBTUSD", flat_history(200))
    r.venue.rows = [breakout_row(), breakout_row(0)]


async def test_a_breakout_buys_twenty_percent_through_plan_then_submit(engine: AsyncEngine) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    report = await r.cycle()
    assert report.strategies[r.rules_id].orders == 1
    state = await r.portfolio(r.rules_id)
    assert len(state.positions) == 1
    position = state.positions[0]
    assert D("19.9") < position.cost_basis <= D(20) and state.fees > 0
    decisions = [d for d in await r.store.decisions(r.rules_id) if d.pair == "XBTUSD"]
    assert [(d.action, d.status, d.reason) for d in decisions] == [
        ("buy", "filled", "breakout_above_48h_high")
    ]
    assert await r.store.planned(r.rules_id) == []
    loop = await r.store.load_state(r.rules_id)
    assert loop is not None and "XBTUSD" in loop.entered_at and "XBTUSD" in loop.last_entry_at


async def test_a_price_collapse_stops_out_and_the_cooldown_blocks_reentry(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    await r.cycle()
    r.venue.scale = D("0.90")  # bids and asks fall 10%, past the -5% stop
    report = await r.cycle(minutes_later=15)
    assert report.strategies[r.rules_id].orders == 1
    assert (await r.portfolio(r.rules_id)).positions == ()
    sells = [d for d in await r.store.decisions(r.rules_id) if d.action == "sell"]
    assert [(d.reason, d.status) for d in sells] == [("stop_loss", "filled")]
    loop = await r.store.load_state(r.rules_id)
    assert loop is not None and "XBTUSD" not in loop.entered_at and "XBTUSD" in loop.last_stop_at
    # Price recovers and the breakout is still on, but the 6 h cooldown holds.
    r.venue.scale = D(1)
    r.venue.rows = [breakout_row(0), breakout_row(900)]
    await r.store.upsert_candles("XBTUSD", [])
    await r.cycle(minutes_later=15)
    last = [d for d in await r.store.decisions(r.rules_id) if d.pair == "XBTUSD"][-1]
    assert last.action == "hold" and last.reason in {"cooldown_after_stop", "candle_gap"}


async def test_positions_are_closed_after_the_maximum_hold_even_when_nothing_else_happens(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    await r.cycle()
    await r.cycle(minutes_later=72 * 60 + 1)  # the fill itself is stamped 1 s after submission
    sells = [d for d in await r.store.decisions(r.rules_id) if d.action == "sell"]
    assert [(d.reason, d.status) for d in sells] == [("max_hold", "filled")]


async def test_a_daily_loss_suspension_blocks_entries_but_not_exits(engine: AsyncEngine) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    suspended = LoopState(
        risk=RiskState(
            peak_equity=D(100), day=r.now.date(), day_open_equity=D(100), daily_suspended=True
        )
    )
    await r.store.save_state(r.rules_id, suspended, r.now)
    report = await r.cycle()
    assert report.strategies[r.rules_id].orders == 0
    assert report.strategies[r.rules_id].blocked == "daily_loss_suspended"
    decisions = [d for d in await r.store.decisions(r.rules_id) if d.pair == "XBTUSD"]
    assert decisions[-1].reason == "daily_loss_suspended"


async def test_a_drawdown_latch_survives_cycles_until_cleared_by_hand(engine: AsyncEngine) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    latched = LoopState(
        risk=RiskState(
            peak_equity=D(100), day=date(2026, 1, 1), day_open_equity=D(100), drawdown_latched=True
        )
    )
    await r.store.save_state(r.rules_id, latched, r.now)
    await r.cycle()
    await r.cycle(minutes_later=15)
    assert (await r.portfolio(r.rules_id)).positions == ()
    kept = await r.store.load_state(r.rules_id)
    assert kept is not None and kept.risk.drawdown_latched


async def test_missing_server_time_means_holds_and_no_orders_not_a_crash(
    engine: AsyncEngine,
) -> None:
    venue = Venue()
    venue.time_down = True
    r = await rig(engine, venue)
    report = await r.cycle()
    assert report.failures and all(o == 0 for o in (s.orders for s in report.strategies.values()))
    reasons = {d.reason for d in await r.store.decisions(r.bench_id)}
    assert reasons == {"no_market_data"}
    assert (await r.portfolio(r.bench_id)).positions == ()


async def test_a_disabled_strategy_is_not_evaluated(engine: AsyncEngine) -> None:
    r = await rig(engine)
    await r.store.set_enabled(r.bench_id, False)
    await r.cycle()
    assert await r.store.decisions(r.bench_id) == []
    assert (await r.portfolio(r.bench_id)).positions == ()
    assert len(await r.store.decisions(r.rules_id)) == 3


async def test_a_crash_after_the_order_but_before_the_bookkeeping_is_recovered_exactly_once(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    # Simulate the crash window: the decision is planned and the paper order is submitted, but the
    # decision was never finished and the loop state never saved.
    from ai_trading_lab.live.engine import build_markets

    async with r.live.transport() as transport:
        from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter

        batch = await KrakenAdapter(transport).collect(PAIRS, candles_since=1)
    market = build_markets(batch, CONFIG)["XBTUSD"]
    row = next(s for s in await r.store.strategies() if s.strategy_id == r.rules_id)
    from ai_trading_lab.paper_execution.models import OrderIntent

    intent = OrderIntent(
        portfolio_id=row.portfolio_id,
        idempotency_key=f"{r.rules_id}:XBTUSD:buy:crash",
        instrument=market.quote.instrument,
        side="buy",
        quantity=D("0.0002"),
        limit_price=D("67067.2"),
        created_at_utc=r.now,
        expires_at_utc=r.now + timedelta(seconds=60),
    )
    await r.store.plan_decision(
        r.rules_id,
        "XBTUSD",
        r.now,
        "buy",
        "breakout_above_48h_high",
        intent.idempotency_key,
        intent.model_dump(mode="json"),
        market.entry.model_dump(mode="json"),
    )
    first = await r.paper.submit(intent, market.quote, r.now, source_entry=market.entry)
    assert first.result.status == "FILLED"
    before = await r.portfolio(r.rules_id)
    # The next cycle must finish that decision without a second fill and restore the state.
    await r.cycle()
    after = await r.portfolio(r.rules_id)
    assert after.positions[0].quantity == before.positions[0].quantity == D("0.0002")
    assert await r.store.planned(r.rules_id) == []
    loop = await r.store.load_state(r.rules_id)
    assert loop is not None and "XBTUSD" in loop.entered_at
    crash = [d for d in await r.store.decisions(r.rules_id) if d.status == "filled"]
    assert len(crash) == 1


async def test_running_the_same_slot_twice_never_doubles_an_order(engine: AsyncEngine) -> None:
    r = await rig(engine)
    await r.cycle()
    first = await r.portfolio(r.bench_id)
    again = await r.live.run_cycle()  # a restart inside the same 15-minute slot
    assert again.strategies[r.bench_id].orders == 0
    assert await r.portfolio(r.bench_id) == first
    filled = [d for d in await r.store.decisions(r.bench_id) if d.action == "buy"]
    assert len(filled) == 3


async def test_pairs_quoted_in_a_different_currency_are_not_traded(engine: AsyncEngine) -> None:
    venue = Venue()
    item = Rig(engine, venue, LiveConfig(pairs=PAIRS, cash_unit="EUR"))
    await item.live.setup()
    report = await item.cycle()
    assert report.observed == []
    assert {d.reason for d in await item.store.decisions(item.bench_id)} == {"no_market_data"}


async def test_an_unreadable_or_unsubmittable_planned_decision_is_finished_not_stuck(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await r.store.plan_decision(
        r.bench_id, "XBTUSD", r.now, "buy", "x", f"{r.bench_id}:bad", {}, {}
    )
    # An intent for a portfolio that does not exist is refused by the ledger, not retried forever.
    from ai_trading_lab.live.engine import build_markets
    from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
    from ai_trading_lab.paper_execution.models import OrderIntent

    async with r.live.transport() as transport:
        batch = await KrakenAdapter(transport).collect(PAIRS, candles_since=1)
    market = build_markets(batch, CONFIG)["XBTUSD"]
    ghost = OrderIntent(
        portfolio_id=uuid4(),
        idempotency_key="ghost",
        instrument=market.quote.instrument,
        side="buy",
        quantity=D("0.0002"),
        limit_price=D("67067.2"),
        created_at_utc=r.now,
        expires_at_utc=r.now + timedelta(seconds=60),
    )
    await r.store.plan_decision(
        r.bench_id,
        "XBTUSD",
        r.now,
        "buy",
        "x",
        f"{r.bench_id}:ghost",
        ghost.model_dump(mode="json"),
        market.entry.model_dump(mode="json"),
    )
    await r.cycle()
    assert await r.store.planned(r.bench_id) == []
    statuses = {d.status for d in await r.store.decisions(r.bench_id) if d.reason == "x"}
    assert statuses == {"rejected"}


async def test_decisions_are_stamped_after_the_evidence_they_rest_on(engine: AsyncEngine) -> None:
    """Found on the first real run: the cycle clock was read before collection, so the first
    order's quote looked like it came from the future and was rejected as unavailable."""
    r = await rig(engine)
    r.now = RECEIVED - timedelta(seconds=5)  # the wall clock lags the captures' receipt times
    report = await r.live.run_cycle()
    assert report.failures == []
    bench = await r.portfolio(r.bench_id)
    assert len(bench.positions) == 3, "every pair must fill, including the first one processed"
    statuses = {d.status for d in await r.store.decisions(r.bench_id) if d.action == "buy"}
    assert statuses == {"filled"}
    assert report.strategies[r.bench_id].equity is not None
    assert report.cycle_at >= RECEIVED


async def test_status_summary_reports_holdings_equity_and_recent_decisions(
    engine: AsyncEngine,
) -> None:
    from ai_trading_lab.live.status import summary

    r = await rig(engine)
    await r.cycle()
    data = await summary(r.live, recent=2)
    assert data["paper_only"] is True
    by_id = {s["strategy_id"]: s for s in data["strategies"]}
    bench = by_id[r.bench_id]
    assert bench["kind"] == "benchmark" and bench["enabled"] is True
    assert len(bench["positions"]) == 3 and D(bench["fees_paid"]) > 0
    assert D(bench["equity"]) < D(100) and bench["decisions_logged"] == 3
    assert len(bench["recent_decisions"]) == 2
    assert by_id[r.rules_id]["positions"] == [] and by_id[r.rules_id]["equity"] is not None
    await r.store.set_enabled(r.bench_id, False)
    again = {s["strategy_id"]: s for s in (await summary(r.live))["strategies"]}
    assert again[r.bench_id]["enabled"] is False  # disabled strategies stay visible


async def portfolio_id(r: Rig, strategy_id: str):  # type: ignore[no-untyped-def]
    return next(
        x for x in await r.store.strategies(enabled_only=False) if x.strategy_id == strategy_id
    ).portfolio_id


async def test_orders_the_broker_would_refuse_are_never_submitted_so_they_cannot_fill_its_history(
    engine: AsyncEngine,
) -> None:
    """Every submitted order, even a rejected one, is a permanent ledger command (capped at 1000).
    A recurring refusal must therefore be caught before submission, not retried into the cap."""
    r = Rig(engine, Venue(), LiveConfig(pairs=PAIRS, cash_unit="USD", max_cost_bps=D(100)))
    await r.live.setup()
    for n in range(3):
        report = await r.cycle(minutes_later=15 if n else 0)
        assert report.strategies[r.bench_id].orders == 0
    assert await r.paper.command_count(await portfolio_id(r, r.bench_id)) == 0
    reasons = {d.reason for d in await r.store.decisions(r.bench_id)}
    assert reasons == {"preflight_max_cost_bps"}


async def test_entries_stop_before_the_ledger_history_is_full_but_exits_do_not(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    monkeypatch.setattr(r.live.paper, "command_count", AsyncMock(return_value=900))
    report = await r.cycle()
    assert report.strategies[r.rules_id].orders == 0
    assert report.strategies[r.rules_id].blocked == "command_capacity"
    assert report.strategies[r.bench_id].orders == 0
    reasons = {d.reason for d in await r.store.decisions(r.bench_id)}
    assert reasons == {"command_capacity"}


async def test_one_strategy_failing_does_not_starve_the_other_and_is_reported(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await rig(engine)

    def boom(**_: object) -> object:
        raise RuntimeError("bug in the benchmark")

    monkeypatch.setattr("ai_trading_lab.live.engine.decide_benchmark", boom)
    report = await r.cycle()
    assert report.errors == [f"{r.bench_id}:RuntimeError"]
    assert len(await r.store.decisions(r.rules_id)) == 3  # the rules strategy still ran
    assert list(report.strategies)[0] == r.rules_id, "the strategy with the exits runs first"


async def test_ledger_integrity_errors_are_fatal_not_business_rejections(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await rig(engine)
    monkeypatch.setattr(
        r.live.paper, "submit", AsyncMock(side_effect=ValueError("Idempotency key conflict"))
    )
    with pytest.raises(LoopIntegrityError, match="Idempotency"):
        await r.cycle()


async def test_a_key_planned_by_another_writer_is_never_submitted_twice(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trading_lab.live.store import PlannedDecision

    r = await rig(engine)

    async def contended(*_: object, **__: object) -> PlannedDecision:
        return PlannedDecision(decision_id=uuid4(), status="planned", created=False)

    monkeypatch.setattr(r.live.store, "plan_decision", contended)
    submit = AsyncMock()
    monkeypatch.setattr(r.live.paper, "submit", submit)
    report = await r.cycle()
    submit.assert_not_awaited()
    assert report.strategies[r.bench_id].filled == 0


async def planned_buy(r: Rig, key: str):  # type: ignore[no-untyped-def]
    from ai_trading_lab.live.engine import build_markets
    from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
    from ai_trading_lab.paper_execution.models import OrderIntent

    async with r.live.transport() as transport:
        batch = await KrakenAdapter(transport).collect(PAIRS, candles_since=1)
    market = build_markets(batch, CONFIG)["XBTUSD"]
    row = next(s for s in await r.store.strategies() if s.strategy_id == r.rules_id)
    intent = OrderIntent(
        portfolio_id=row.portfolio_id,
        idempotency_key=key,
        instrument=market.quote.instrument,
        side="buy",
        quantity=D("0.0002"),
        limit_price=D("67067.2"),
        created_at_utc=r.now,
        expires_at_utc=r.now + timedelta(seconds=60),
    )
    planned = await r.store.plan_decision(
        r.rules_id,
        "XBTUSD",
        r.now,
        "buy",
        "breakout_above_48h_high",
        key,
        intent.model_dump(mode="json"),
        market.entry.model_dump(mode="json"),
    )
    return market, intent, planned


async def test_a_crash_after_planning_but_before_submitting_resolves_safely(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    _, _, planned = await planned_buy(r, f"{r.rules_id}:XBTUSD:buy:lost")
    # Several minutes later the stored quote is stale: the order must be refused, not filled at
    # an old price, and the decision must not stay planned forever.
    await r.cycle(minutes_later=5)
    assert await r.store.planned(r.rules_id) == []
    lost = [d for d in await r.store.decisions(r.rules_id) if d.decision_id == planned.decision_id]
    assert [(d.status, (d.result or {}).get("reason")) for d in lost] == [
        ("rejected", "order_expired")  # the intent's 60 s life ended before the retry
    ]


async def test_a_crash_after_saving_state_but_before_finishing_is_idempotent(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    market, intent, planned = await planned_buy(r, f"{r.rules_id}:XBTUSD:buy:half")
    receipt = await r.paper.submit(intent, market.quote, r.now, source_entry=market.entry)
    state = LoopState(
        risk=RiskState(peak_equity=D(100), day=r.now.date(), day_open_equity=D(100)),
        entered_at={"XBTUSD": receipt.result.executed_at_utc},
        last_entry_at={"XBTUSD": receipt.result.executed_at_utc},
    )
    await r.store.save_state(r.rules_id, state, r.now)  # state saved, decision never finished
    await r.cycle()
    assert await r.store.planned(r.rules_id) == []
    done = [d for d in await r.store.decisions(r.rules_id) if d.decision_id == planned.decision_id]
    assert [d.status for d in done] == ["filled"]
    loop = await r.store.load_state(r.rules_id)
    assert loop is not None and loop.entered_at["XBTUSD"] == receipt.result.executed_at_utc
    assert len((await r.portfolio(r.rules_id)).positions) == 1


async def test_the_daily_suspension_clears_at_the_next_utc_day_inside_the_engine(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    suspended = LoopState(
        risk=RiskState(
            peak_equity=D(100), day=r.now.date(), day_open_equity=D(100), daily_suspended=True
        )
    )
    await r.store.save_state(r.rules_id, suspended, r.now)
    first = await r.cycle()
    assert first.strategies[r.rules_id].blocked == "daily_loss_suspended"
    later = await r.cycle(minutes_later=24 * 60)
    assert later.strategies[r.rules_id].blocked is None
    loop = await r.store.load_state(r.rules_id)
    assert loop is not None and loop.risk.day == r.now.date() and not loop.risk.daily_suspended


async def test_a_disabled_strategy_winds_down_it_still_exits_but_never_enters(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    await r.cycle()
    assert len((await r.portfolio(r.rules_id)).positions) == 1
    await r.store.set_enabled(r.rules_id, False, actor="test", reason="pause")
    before = len(await r.store.decisions(r.rules_id))
    report = await r.cycle(minutes_later=15)
    assert report.strategies[r.rules_id].blocked == "strategy_disabled"
    assert report.strategies[r.rules_id].orders == 0
    # Quiet cycles journal nothing for a strategy that is off, and the position is kept.
    assert len(await r.store.decisions(r.rules_id)) == before
    assert len((await r.portfolio(r.rules_id)).positions) == 1
    # The 72 h limit is a mechanical exit: it still fires while the strategy is off.
    await r.cycle(minutes_later=72 * 60)
    assert (await r.portfolio(r.rules_id)).positions == ()
    exits = [d for d in await r.store.decisions(r.rules_id) if d.action == "sell"]
    assert [(d.reason, d.status) for d in exits] == [("max_hold", "filled")]


async def test_a_disabled_strategy_makes_no_new_entry_and_resumes_when_enabled(
    engine: AsyncEngine,
) -> None:
    r = await rig(engine)
    await seed_breakout(r)
    await r.store.set_enabled(r.rules_id, False, actor="test", reason="pause")
    report = await r.cycle()
    assert report.strategies[r.rules_id].orders == 0
    assert (await r.portfolio(r.rules_id)).positions == ()
    assert report.strategies[r.rules_id].equity is not None  # the equity curve continues
    assert await r.store.decisions(r.rules_id) == []
    await r.store.set_enabled(r.rules_id, True, actor="test", reason="resume")
    again = await r.cycle()  # the same market: the breakout is still there
    assert again.strategies[r.rules_id].blocked is None
    assert again.strategies[r.rules_id].orders == 1


async def test_a_planned_buy_left_by_a_crash_is_never_sent_once_the_strategy_is_off(
    engine: AsyncEngine,
) -> None:
    from ai_trading_lab.live.engine import build_markets
    from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
    from ai_trading_lab.paper_execution.models import OrderIntent

    r = await rig(engine)
    await seed_breakout(r)
    async with r.live.transport() as transport:
        batch = await KrakenAdapter(transport).collect(PAIRS, candles_since=1)
    market = build_markets(batch, CONFIG)["XBTUSD"]
    row = next(s for s in await r.store.strategies() if s.strategy_id == r.rules_id)
    intent = OrderIntent(
        portfolio_id=row.portfolio_id,
        idempotency_key=f"{r.rules_id}:XBTUSD:buy:crash",
        instrument=market.quote.instrument,
        side="buy",
        quantity=D("0.0002"),
        limit_price=D("67067.2"),
        created_at_utc=r.now,
        expires_at_utc=r.now + timedelta(seconds=60),
    )
    await r.store.plan_decision(
        r.rules_id,
        "XBTUSD",
        r.now,
        "buy",
        "breakout_above_48h_high",
        intent.idempotency_key,
        intent.model_dump(mode="json"),
        market.entry.model_dump(mode="json"),
    )
    await r.store.set_enabled(r.rules_id, False, actor="test", reason="stop before restart")
    await r.cycle()
    assert (await r.portfolio(r.rules_id)).positions == ()
    decisions = await r.store.decisions(r.rules_id)
    assert [(d.status, (d.result or {}).get("error")) for d in decisions] == [
        ("rejected", "strategy_disabled")
    ]


async def test_health_follows_the_equity_the_loop_records(engine: AsyncEngine) -> None:
    from ai_trading_lab.live.status import health

    r = await rig(engine)
    stale_limit = timedelta(minutes=40)
    never = await health(r.live, r.now, stale_limit)
    assert never["healthy"] is False and never["reason"] == "no_equity_recorded_yet"
    await r.cycle()
    ok = await health(r.live, r.now, stale_limit)
    assert ok["healthy"] is True and ok["reason"] == "ok"
    later = await health(r.live, r.now + timedelta(minutes=41), stale_limit)
    assert later["healthy"] is False and later["reason"] == "stale"
    # A switched-off strategy still records equity, so being off is not "unhealthy".
    await r.store.set_enabled(r.rules_id, False, actor="test", reason="pause")
    await r.store.set_enabled(r.bench_id, False, actor="test", reason="pause")
    await r.cycle(minutes_later=15)
    assert (await health(r.live, r.now, stale_limit))["healthy"] is True


async def clear_journal(engine: AsyncEngine) -> None:
    from sqlalchemy import delete as sql_delete

    from ai_trading_lab.live.journal import error_groups, error_occurrences

    async with engine.begin() as connection:
        await connection.execute(sql_delete(error_occurrences))
        await connection.execute(sql_delete(error_groups))


async def test_a_strategy_exception_is_journaled_with_detail_and_the_others_still_run(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await clear_journal(engine)
    r = await rig(engine)
    real = r.live._step

    async def failing(strategy, markets, now):  # type: ignore[no-untyped-def]
        if strategy.strategy_id == r.rules_id:
            raise ValueError("bad candle, api_key=SuperSecretValue123")
        return await real(strategy, markets, now)

    monkeypatch.setattr(r.live, "_step", failing)
    report = await r.cycle()
    assert report.errors == [f"{r.rules_id}:ValueError"]
    assert report.strategies[r.bench_id].orders == 3  # the benchmark was not starved
    (group,) = await r.live.journal.groups(level="error")
    assert (group.component, group.error_type, group.status) == (
        "rules_strategy",
        "ValueError",
        "new",
    )
    assert group.last_strategy_id == r.rules_id
    assert "SuperSecretValue123" not in (group.last_message or "")
    assert "SuperSecretValue123" not in (group.last_traceback or "")
    assert "failing" in (group.last_traceback or "")


async def test_an_unreachable_exchange_is_a_journal_warning_not_an_error(
    engine: AsyncEngine,
) -> None:
    await clear_journal(engine)
    r = await rig(engine)
    r.venue.time_down = True
    report = await r.cycle()
    assert report.failures
    warnings = await r.live.journal.groups(level="warning")
    assert {g.component for g in warnings} == {"data_collection"}
    assert any(g.error_type == "network_error" for g in warnings)
    assert all(g.status == "seen" for g in warnings)
    summary = await r.live.journal.summary()
    assert summary.unacknowledged_errors == 0 and summary.warnings_24h >= 1


async def test_an_integrity_failure_is_journaled_before_the_loop_stops(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await clear_journal(engine)
    r = await rig(engine)
    monkeypatch.setattr(
        r.live.paper, "submit", AsyncMock(side_effect=ValueError("Idempotency key conflict"))
    )
    with pytest.raises(LoopIntegrityError):
        await r.cycle()
    (group,) = await r.live.journal.groups(level="error")
    assert group.component == "paper_broker" and group.error_type == "LoopIntegrityError"
