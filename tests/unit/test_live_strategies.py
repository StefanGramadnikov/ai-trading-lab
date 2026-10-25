"""Rules and benchmark decisions over real-shaped evidence; pure, no database."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.risk import RiskState
from ai_trading_lab.live.store import LoopState
from ai_trading_lab.live.strategies import (
    Mark,
    Market,
    decide_benchmark,
    decide_rules,
    liquidation,
)
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.paper_execution.accounting import PortfolioState, Position
from tests.contract.test_kraken import RECEIVED
from tests.unit.test_kraken_evidence import entry
from tests.unit.test_live_logic import D, series

NOW = RECEIVED + timedelta(seconds=1)
CONFIG = LiveConfig(pairs=("XBTUSD",), cash_unit="USD")
PAIR = "XBTUSD"


def flat_candles(last: tuple[str, str, str, str] | None = None) -> list[Candle]:
    # tests.unit.test_live_logic builds candles ending at ITS own NOW; shift them to ours.
    shift = NOW - datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    return [
        c.model_copy(
            update={
                "open_time_utc": c.open_time_utc + shift,
                "close_time_utc": c.close_time_utc + shift,
            }
        )
        for c in series(last=last)
    ]


BREAKOUT = ("69000", "67000", "68500", "25")


async def market() -> Market:
    item = await entry()
    return Market(pair=PAIR, entry=item, quote=item.quote, rules=item.rules)


def portfolio(
    market_: Market, *, cash: str = "100", quantity: str | None = None, basis: str = "0"
) -> PortfolioState:
    positions: tuple[Position, ...] = ()
    if quantity is not None:
        positions = (
            Position(
                instrument=market_.quote.instrument, quantity=D(quantity), cost_basis=D(basis)
            ),
        )
    return PortfolioState(
        portfolio_id=UUID(int=1),
        cash_unit="USD",
        cash_step=D("0.00001"),
        initial_cash=D(100),
        cash=D(cash),
        positions=positions,
    )


def loop(**changes: object) -> LoopState:
    base = LoopState(
        risk=RiskState(peak_equity=D(100), day=date(2026, 1, 1), day_open_equity=D(100)),
    )
    return base.model_copy(update=changes)


async def rules(markets: dict[str, Market] | None = None, **changes: object):  # type: ignore[no-untyped-def]
    m = await market()
    values: dict[str, object] = {
        "config": CONFIG,
        "now": NOW,
        "markets": {PAIR: m} if markets is None else markets,
        "candles": {PAIR: flat_candles(BREAKOUT)},
        "state": portfolio(m),
        "loop": loop(),
        "marks": {},
        "equity": D(100),
        "blocked": None,
    }
    return decide_rules(**(values | changes))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_breakout_buys_twenty_percent_with_a_marketable_limit() -> None:
    (decision,) = await rules()
    assert decision.action == "buy" and decision.reason == "breakout_above_48h_high"
    order = decision.order
    assert order is not None and order.side == "buy"
    assert order.limit_price == D("67067.2")  # best ask 67000.1 plus 10 bps on the 0.1 tick
    assert order.quantity * order.limit_price <= D(20)
    assert order.quantity * order.limit_price > D("19.99")


@pytest.mark.asyncio
async def test_no_market_data_and_unqualified_quotes_never_trade() -> None:
    (none,) = await rules(markets={})
    assert (none.action, none.reason, none.order) == ("hold", "no_market_data", None)
    m = await market()
    partial = m.quote.model_copy(update={"quality": DataQuality.PARTIAL})
    (decision,) = await rules(markets={PAIR: Market(PAIR, m.entry, partial, m.rules)})
    assert (decision.action, decision.reason) == ("hold", "quote_not_valid")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["drawdown_latched", "daily_loss_suspended"])
async def test_risk_blocks_stop_entries_with_the_reason(blocked: str) -> None:
    (decision,) = await rules(blocked=blocked)
    assert (decision.action, decision.reason) == ("hold", blocked)


@pytest.mark.asyncio
async def test_unknown_equity_blocks_entries_fail_closed() -> None:
    (decision,) = await rules(equity=None)
    assert (decision.action, decision.reason) == ("hold", "equity_unavailable")


@pytest.mark.asyncio
async def test_signal_reasons_pass_through_when_there_is_no_breakout_or_history() -> None:
    (flat,) = await rules(candles={PAIR: flat_candles()})
    assert (flat.action, flat.reason) == ("hold", "no_breakout")
    (short,) = await rules(candles={PAIR: flat_candles(BREAKOUT)[:50]})
    assert short.reason == "insufficient_history"


@pytest.mark.asyncio
async def test_cooldown_after_a_stop_and_the_daily_entry_limit_block_reentry() -> None:
    (cool,) = await rules(loop=loop(last_stop_at={PAIR: NOW - timedelta(hours=2)}))
    assert cool.reason == "cooldown_after_stop"
    (recent,) = await rules(loop=loop(last_entry_at={PAIR: NOW - timedelta(hours=10)}))
    assert recent.reason == "entry_interval"


@pytest.mark.asyncio
async def test_exposure_already_used_by_other_holdings_limits_the_new_entry() -> None:
    m = await market()
    other = m.quote.instrument.model_copy(update={"native_id": "OTHERPAIR"})
    state = PortfolioState(
        portfolio_id=UUID(int=1),
        cash_unit="USD",
        cash_step=D("0.00001"),
        initial_cash=D(100),
        cash=D(70),
        positions=(Position(instrument=other, quantity=D("1"), cost_basis=D("30")),),
    )
    (decision,) = await rules(state=state)
    assert decision.order is not None
    notional = decision.order.quantity * decision.order.limit_price
    assert D("5") < notional <= D("10")  # 40% of equity minus the 30 already invested


def held(m: Market, *, entry_price: str = "67000", quantity: str = "0.0003"):  # type: ignore[no-untyped-def]
    basis = (D(entry_price) * D(quantity)).quantize(D("0.00001"))
    return portfolio(m, cash=str(D(100) - basis), quantity=quantity, basis=str(basis))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mark", "entered_hours", "reason"),
    [
        ("63650.0", 1, "stop_loss"),
        ("72360.0", 1, "take_profit"),
        ("67000.0", 72, "max_hold"),
    ],
)
async def test_mechanical_exits_sell_the_whole_position_even_when_entries_are_blocked(
    mark: str, entered_hours: int, reason: str
) -> None:
    m = await market()
    (decision,) = await rules(
        state=held(m),
        marks={PAIR: Mark(price=D(mark), net=D(mark) * D("0.0003"))},
        loop=loop(entered_at={PAIR: NOW - timedelta(hours=entered_hours)}),
        blocked="drawdown_latched",
        equity=None,
    )
    assert (decision.action, decision.reason) == ("sell", reason)
    order = decision.order
    assert order is not None and order.side == "sell" and order.quantity == D("0.0003")
    assert order.limit_price == D("66932.9")  # best bid 66999.9 minus 10 bps on the tick


@pytest.mark.asyncio
async def test_a_trend_break_exits_and_an_unmarkable_position_is_flagged_not_assumed() -> None:
    m = await market()
    broken = flat_candles(("100", "90", "90", "25"))  # closes below the prior 24 h low (99)
    mark = {PAIR: Mark(price=D("67000"), net=D("20"))}
    (trend,) = await rules(
        state=held(m), marks=mark, candles={PAIR: broken}, loop=loop(entered_at={PAIR: NOW})
    )
    assert (trend.action, trend.reason) == ("sell", "trend_break_below_24h_low")
    (blind,) = await rules(state=held(m), marks={PAIR: None}, loop=loop(entered_at={PAIR: NOW}))
    assert (blind.action, blind.reason) == ("hold", "unmarkable_position")
    (calm,) = await rules(state=held(m), marks=mark, loop=loop(entered_at={PAIR: NOW}))
    assert (calm.action, calm.reason) == ("hold", "holding")


@pytest.mark.asyncio
async def test_a_position_with_no_recorded_entry_time_is_closed_not_ignored() -> None:
    m = await market()
    (decision,) = await rules(
        state=held(m), marks={PAIR: Mark(price=D("67000"), net=D("20"))}, loop=loop()
    )
    assert (decision.action, decision.reason) == ("sell", "max_hold")


@pytest.mark.asyncio
async def test_liquidation_walks_the_bids_and_marks_unmarkable_when_depth_is_short() -> None:
    m = await market()
    policy = CONFIG.policy()
    position = Position(instrument=m.quote.instrument, quantity=D("0.0003"), cost_basis=D("20"))
    mark = liquidation(position, m.quote, policy, NOW)
    assert mark is not None
    gross = mark.price * D("0.0003")
    assert D("66999") < mark.price <= D("67000")
    assert mark.net == gross - (gross * D("0.008")).quantize(D("0.00001"), rounding="ROUND_UP")
    too_big = position.model_copy(update={"quantity": D("10")})
    assert liquidation(too_big, m.quote, policy, NOW) is None
    assert liquidation(position, m.quote, policy, NOW + timedelta(minutes=5)) is None  # stale


@pytest.mark.asyncio
async def test_benchmark_buys_each_pair_once_and_never_sells() -> None:
    m = await market()
    config = LiveConfig(pairs=(PAIR, "ETHUSD", "SOLUSD"), cash_unit="USD")
    decisions = decide_benchmark(
        config=config, now=NOW, markets={PAIR: m}, state=portfolio(m), loop=loop()
    )
    buy = next(d for d in decisions if d.pair == PAIR)
    assert buy.action == "buy" and buy.reason == "benchmark_entry" and buy.order is not None
    spend = buy.order.quantity * buy.order.limit_price
    assert D("33") < spend <= D("100") / 3  # one third of capital, fee included
    # Pairs with no evidence this cycle are logged holds, never guesses.
    assert {d.pair: d.reason for d in decisions if d.pair != PAIR} == {
        "ETHUSD": "no_market_data",
        "SOLUSD": "no_market_data",
    }
    done = decide_benchmark(config=config, now=NOW, markets={PAIR: m}, state=held(m), loop=loop())
    assert next(d for d in done if d.pair == PAIR).reason == "benchmark_holding"
    assert all(d.order is None for d in done)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_price", "held_hours", "reason"),
    [("72000", 1, "stop_loss"), ("67000", 72, "max_hold")],
)
async def test_mechanical_exits_still_fire_when_the_position_cannot_be_fully_valued(
    entry_price: str, held_hours: int, reason: str
) -> None:
    """A thin or stressed book must not disable a stop: exits fall back to the touch price."""
    m = await market()
    (decision,) = await rules(
        state=held(m, entry_price=entry_price),
        marks={PAIR: None},
        loop=loop(entered_at={PAIR: NOW - timedelta(hours=held_hours)}),
        equity=None,
    )
    assert (decision.action, decision.reason) == ("sell", reason)
    assert decision.order is not None and decision.order.quantity == D("0.0003")


@pytest.mark.asyncio
async def test_same_cycle_entries_share_the_exposure_ceiling_in_config_order() -> None:
    m = await market()
    config = LiveConfig(pairs=("XBTUSD", "ETHUSD", "SOLUSD"), cash_unit="USD")
    markets = {p: Market(p, m.entry, m.quote, m.rules) for p in config.pairs}
    candles = {p: flat_candles(BREAKOUT) for p in config.pairs}
    decisions = decide_rules(
        config=config,
        now=NOW,
        markets=markets,
        candles=candles,
        state=portfolio(m),
        loop=loop(),
        marks={},
        equity=D(100),
        blocked=None,
    )
    spent = [d.order.quantity * d.order.limit_price for d in decisions if d.order is not None]
    assert len(spent) == 2 and all(D("19.9") < x <= D(20) for x in spent)
    assert sum(spent) <= D(40)  # 40% of equity, never three 20% entries
    assert [d.reason for d in decisions][-1] == "size_below_minimum_or_no_room"
