"""Pure live-loop decisions: breakout signal, exits, sizing and risk state (DEC-043 numbers)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ai_trading_lab.live.config import BreakoutConfig, LiveConfig, RiskConfig
from ai_trading_lab.live.risk import (
    RiskState,
    entry_gate,
    exit_reason,
    marketable_limit,
    size_entry,
    update_risk,
)
from ai_trading_lab.live.signals import breakout_signal
from ai_trading_lab.market_data.adapters.kraken import Candle

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
D = Decimal


def candle(
    index: int, count: int, *, high: str, low: str, close: str, volume: str = "10"
) -> Candle:
    """Candle `index` of `count`, the last one closing exactly at NOW."""
    close_time = NOW - timedelta(minutes=15 * (count - 1 - index))
    return Candle(
        open_time_utc=close_time - timedelta(minutes=15),
        close_time_utc=close_time,
        interval_minutes=15,
        open=D(close),
        high=D(high),
        low=D(low),
        close=D(close),
        vwap=D(close),
        volume=D(volume),
        trades=5,
    )


def series(count: int = 200, *, last: tuple[str, str, str, str] | None = None) -> list[Candle]:
    rows = [candle(i, count, high="101", low="99", close="100") for i in range(count)]
    if last is not None:
        high, low, close, volume = last
        rows[-1] = candle(count - 1, count, high=high, low=low, close=close, volume=volume)
    return rows


# ---- config -----------------------------------------------------------------------------


def test_defaults_are_exactly_the_approved_dec_043_numbers() -> None:
    config = LiveConfig()
    risk = config.risk
    assert config.pairs == ("XBTEUR", "ETHEUR", "SOLEUR") and config.cash_unit == "EUR"
    assert config.initial_cash == D(100) and config.fee_bps == D(80)
    assert (risk.position_fraction, risk.exposure_fraction) == (D("0.20"), D("0.40"))
    assert (risk.daily_loss_fraction, risk.drawdown_fraction) == (D("0.03"), D("0.10"))
    assert (risk.stop_loss, risk.take_profit) == (D("0.05"), D("0.08"))
    assert (risk.max_hold_hours, risk.cooldown_hours, risk.entry_interval_hours) == (72, 6, 24)
    policy = config.policy()
    assert policy.fee_bps == D(80) and policy.max_order_notional == D(20)
    assert policy.max_position_notional == D(20) and policy.max_total_exposure == D(40)


@pytest.mark.parametrize(
    "change",
    [
        {"position_fraction": "0"},
        {"position_fraction": "1.5"},
        {"stop_loss": "0"},
        {"take_profit": "-1"},
        {"max_hold_hours": 0},
        {"exposure_fraction": "0.1"},  # below one position
    ],
)
def test_risk_config_rejects_nonsense(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RiskConfig.model_validate(RiskConfig().model_dump() | change)


# ---- breakout signal --------------------------------------------------------------------


def test_buy_on_a_close_above_the_prior_48h_high_with_volume() -> None:
    signal = breakout_signal(series(last=("103", "100", "102", "25")), held=False, now=NOW)
    assert signal.action == "buy" and signal.reason == "breakout_above_48h_high"


@pytest.mark.parametrize(
    ("last", "reason"),
    [
        (("101", "99", "101", "25"), "no_breakout"),  # equal to the prior high is not above it
        (("103", "100", "102", "5"), "volume_below_median"),
    ],
)
def test_no_buy_without_a_strict_breakout_and_volume(
    last: tuple[str, str, str, str], reason: str
) -> None:
    signal = breakout_signal(series(last=last), held=False, now=NOW)
    assert signal.action == "hold" and signal.reason == reason


def test_no_entry_when_already_holding_and_exit_on_a_24h_trend_break() -> None:
    assert (
        breakout_signal(series(last=("103", "100", "102", "25")), held=True, now=NOW).action
        == "hold"
    )
    broken = series(last=("100", "97", "98", "25"))
    signal = breakout_signal(broken, held=True, now=NOW)
    assert signal.action == "sell" and signal.reason == "trend_break_below_24h_low"
    assert breakout_signal(broken, held=False, now=NOW).action == "hold"


def test_missing_stale_or_gapped_candles_never_trade() -> None:
    assert breakout_signal(series(100), held=False, now=NOW).reason == "insufficient_history"
    stale = series(last=("103", "100", "102", "25"))
    assert (
        breakout_signal(stale, held=False, now=NOW + timedelta(hours=2)).reason == "stale_candles"
    )
    gapped = series(last=("103", "100", "102", "25"))
    del gapped[150]
    assert breakout_signal(gapped, held=False, now=NOW).reason == "candle_gap"


def test_breakout_parameters_are_versioned_and_bounded() -> None:
    assert BreakoutConfig().version == "breakout-v1"
    with pytest.raises(ValueError):
        BreakoutConfig(entry_lookback=3)


# ---- exits ------------------------------------------------------------------------------

ENTERED = NOW - timedelta(hours=1)


@pytest.mark.parametrize(
    ("price", "held_hours", "expected"),
    [
        ("100", 1, None),
        ("95.01", 1, None),
        ("95", 1, "stop_loss"),
        ("94", 1, "stop_loss"),
        ("107.99", 1, None),
        ("108", 1, "take_profit"),
        ("100", 72, "max_hold"),
        ("100", 71, None),
        ("90", 80, "stop_loss"),  # the most urgent reason wins
    ],
)
def test_deterministic_exits(price: str, held_hours: int, expected: str | None) -> None:
    entered = NOW - timedelta(hours=held_hours)
    assert exit_reason(D("100"), D(price), entered, NOW, RiskConfig()) == expected


# ---- order prices and sizing -----------------------------------------------------------


def test_marketable_limits_sit_ten_bps_beyond_the_touch_on_the_tick() -> None:
    assert marketable_limit("buy", D("67000.1"), D("0.1"), 10) == D("67067.2")
    assert marketable_limit("sell", D("66999.9"), D("0.1"), 10) == D("66932.9")


def sizing(**changes: object) -> Decimal | None:
    values: dict[str, object] = {
        "equity": D(100),
        "exposure_basis": D(0),
        "cash": D(100),
        "limit_price": D("67067.2"),
        "quantity_step": D("0.00000001"),
        "order_min": D("0.00006"),
        "cost_min": D("0.45"),
        "policy_cap": D(20),
        "fee_bps": D(80),
        "risk": RiskConfig(),
    }
    return size_entry(**(values | changes))  # type: ignore[arg-type]


def test_entry_is_twenty_percent_of_equity_and_never_exceeds_cash_or_caps() -> None:
    quantity = sizing()
    assert quantity is not None and quantity * D("67067.2") <= D(20)
    assert quantity * D("67067.2") > D("19.99")
    assert quantity % D("0.00000001") == 0
    # Equity above the starting capital is still held to the policy cap.
    bigger = sizing(equity=D(500), cash=D(500))
    assert bigger is not None and bigger * D("67067.2") <= D(20)
    # Less equity means a smaller position, and the exposure ceiling binds.
    smaller = sizing(equity=D(50), cash=D(50))
    assert smaller is not None and smaller < quantity
    left = sizing(exposure_basis=D("35"))
    assert left is not None and left * D("67067.2") <= D(5)


@pytest.mark.parametrize(
    "change",
    [
        {"exposure_basis": D(40)},
        {"cash": D("0.1")},
        {"equity": D("0.5"), "cash": D("0.5")},
        {"limit_price": D("1000000")},  # one step is bigger than the position
    ],
)
def test_no_entry_when_nothing_fits(change: dict[str, object]) -> None:
    assert sizing(**change) is None


def test_cash_covers_the_notional_plus_the_fee() -> None:
    quantity = sizing(cash=D("10"))
    assert quantity is not None
    notional = quantity * D("67067.2")
    assert notional + notional * D("0.008") <= D(10)


# ---- entry gate --------------------------------------------------------------------------


def gate(**changes: object) -> str | None:
    values: dict[str, object] = {
        "blocked": None,
        "held": False,
        "last_entry_at": None,
        "last_stop_at": None,
        "now": NOW,
        "risk": RiskConfig(),
    }
    return entry_gate(**(values | changes))  # type: ignore[arg-type]


def test_entry_gate_blocks_for_holdings_cooldowns_and_the_daily_entry_limit() -> None:
    assert gate() is None
    assert gate(held=True) == "already_held"
    assert gate(last_stop_at=NOW - timedelta(hours=5, minutes=59)) == "cooldown_after_stop"
    assert gate(last_stop_at=NOW - timedelta(hours=6)) is None
    assert gate(last_entry_at=NOW - timedelta(hours=23, minutes=59)) == "entry_interval"
    assert gate(last_entry_at=NOW - timedelta(hours=24)) is None


# ---- risk state --------------------------------------------------------------------------


def fresh(equity: str = "100") -> RiskState:
    return RiskState(
        peak_equity=D(equity),
        day=date(2026, 1, 10),
        day_open_equity=D(equity),
        drawdown_latched=False,
    )


def test_daily_loss_suspends_entries_for_the_rest_of_the_utc_day_only() -> None:
    risk = RiskConfig()
    state, blocked = update_risk(fresh(), D("97.01"), NOW, D(100), risk)
    assert blocked is None
    state, blocked = update_risk(state, D("97"), NOW, D(100), risk)
    assert blocked == "daily_loss_suspended"
    # Still suspended later the same day, even after a partial recovery.
    state, blocked = update_risk(state, D("99"), NOW + timedelta(hours=3), D(100), risk)
    assert blocked == "daily_loss_suspended"
    next_day = NOW + timedelta(days=1)
    state, blocked = update_risk(state, D("98"), next_day, D(100), risk)
    assert blocked is None and state.day == next_day.date() and state.day_open_equity == D(98)


def test_drawdown_latches_until_manually_cleared_and_peak_only_rises() -> None:
    risk = RiskConfig()
    state, _ = update_risk(fresh(), D("120"), NOW, D(100), risk)
    assert state.peak_equity == D(120)
    state, blocked = update_risk(state, D("108"), NOW + timedelta(hours=1), D(100), risk)
    assert blocked == "drawdown_latched" and state.drawdown_latched
    state, blocked = update_risk(state, D("125"), NOW + timedelta(hours=2), D(100), risk)
    assert blocked == "drawdown_latched", "recovery never clears a latch"
    assert state.peak_equity == D(125)
    cleared = state.model_copy(update={"drawdown_latched": False, "peak_equity": D(125)})
    assert update_risk(cleared, D("125"), NOW + timedelta(hours=3), D(100), risk)[1] is None


def test_gate_reports_the_risk_block() -> None:
    assert gate(blocked="drawdown_latched") == "drawdown_latched"
