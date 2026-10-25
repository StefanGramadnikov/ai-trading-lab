"""The rules-only control strategy: a pure function of closed candles."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from ai_trading_lab.live.config import BreakoutConfig
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import FrozenModel


class Signal(FrozenModel):
    action: Literal["buy", "sell", "hold"]
    reason: str


def _hold(reason: str) -> Signal:
    return Signal(action="hold", reason=reason)


def breakout_signal(
    candles: Sequence[Candle],
    *,
    held: bool,
    now: datetime,
    config: BreakoutConfig | None = None,
) -> Signal:
    """Buy a close above the prior 48 h high on at least median volume; exit a 24 h trend break.

    Missing, stale or gapped candles never trade: absence of data is not evidence of a quiet market.
    """
    config = config or BreakoutConfig()
    needed = config.entry_lookback + 1
    if len(candles) < needed:
        return _hold("insufficient_history")
    window = list(candles[-needed:])
    step = timedelta(minutes=config.interval_minutes)
    if any(
        later.open_time_utc - earlier.open_time_utc != step
        for earlier, later in zip(window, window[1:], strict=False)
    ) or any(c.interval_minutes != config.interval_minutes for c in window):
        return _hold("candle_gap")
    last, prior = window[-1], window[:-1]
    if now - last.close_time_utc > 2 * step:
        return _hold("stale_candles")
    if held:
        exit_window = prior[-config.exit_lookback :]
        if last.close < min(c.low for c in exit_window):
            return Signal(action="sell", reason="trend_break_below_24h_low")
        return _hold("holding")
    if last.close <= max(c.high for c in prior):
        return _hold("no_breakout")
    if last.volume < median(c.volume for c in prior) * config.min_volume_ratio:
        return _hold("volume_below_median")
    return Signal(action="buy", reason="breakout_above_48h_high")
