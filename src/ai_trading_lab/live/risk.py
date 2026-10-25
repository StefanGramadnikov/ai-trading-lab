"""Deterministic risk, exits and sizing for the live loop. Pure functions; no AI input."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from ai_trading_lab.live.config import RiskConfig
from ai_trading_lab.market_data.models import FrozenModel

BPS = Decimal(10000)
CASH_MARGIN = Decimal("0.0001")  # keeps rounded-up fees from overdrawing cash by a cash step


class RiskState(FrozenModel):
    """Equity is cash plus the net proceeds of liquidating every position at the current book."""

    peak_equity: Decimal
    day: date
    day_open_equity: Decimal
    drawdown_latched: bool = False
    daily_suspended: bool = False


def update_risk(
    state: RiskState, equity: Decimal, now: datetime, initial: Decimal, risk: RiskConfig
) -> tuple[RiskState, str | None]:
    """Advance the risk state and return the reason new entries are blocked, if any.

    Daily loss is mark-to-market equity against the day's opening equity, measured against the
    initial capital, and suspends entries for the rest of that UTC day. Drawdown is peak-to-now
    and latches until it is cleared by hand. Exits are never blocked by either.
    """
    today = now.date()
    if today != state.day:
        state = state.model_copy(
            update={"day": today, "day_open_equity": equity, "daily_suspended": False}
        )
    peak = max(state.peak_equity, equity)
    latched = state.drawdown_latched or (peak - equity) >= peak * risk.drawdown_fraction
    suspended = state.daily_suspended or (
        state.day_open_equity - equity >= initial * risk.daily_loss_fraction
    )
    state = state.model_copy(
        update={"peak_equity": peak, "drawdown_latched": latched, "daily_suspended": suspended}
    )
    if latched:
        return state, "drawdown_latched"
    if suspended:
        return state, "daily_loss_suspended"
    return state, None


def exit_reason(
    entry_price: Decimal,
    mark_price: Decimal,
    entered_at: datetime,
    now: datetime,
    risk: RiskConfig,
) -> Literal["stop_loss", "take_profit", "max_hold"] | None:
    """Mechanical exits against the average entry price. The most urgent reason wins."""
    if mark_price <= entry_price * (1 - risk.stop_loss):
        return "stop_loss"
    if mark_price >= entry_price * (1 + risk.take_profit):
        return "take_profit"
    if now - entered_at >= timedelta(hours=risk.max_hold_hours):
        return "max_hold"
    return None


def marketable_limit(
    side: Literal["buy", "sell"], touch: Decimal, tick: Decimal, bps: int
) -> Decimal:
    """A limit just beyond the touch so an immediate order can walk the book without chasing it."""
    if side == "buy":
        raw = touch * (1 + Decimal(bps) / BPS)
        return (raw / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    raw = touch * (1 - Decimal(bps) / BPS)
    return (raw / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def size_entry(
    *,
    equity: Decimal,
    exposure_basis: Decimal,
    cash: Decimal,
    limit_price: Decimal,
    quantity_step: Decimal,
    order_min: Decimal,
    cost_min: Decimal,
    policy_cap: Decimal,
    fee_bps: Decimal,
    risk: RiskConfig,
) -> Decimal | None:
    """Quantity for a new entry, or None when nothing fits.

    The notional is the smallest of: the position fraction of equity, the room left under the
    total-exposure ceiling, the paper broker's per-order cap, and the cash left after the fee.
    """
    target = min(
        equity * risk.position_fraction,
        equity * risk.exposure_fraction - exposure_basis,
        policy_cap,
        (cash - CASH_MARGIN) / (1 + fee_bps / BPS),
    )
    if target <= 0 or limit_price <= 0:
        return None
    quantity = (target / limit_price / quantity_step).to_integral_value(
        rounding=ROUND_FLOOR
    ) * quantity_step
    if quantity < order_min or quantity * limit_price < cost_min:
        return None
    return quantity


def entry_gate(
    *,
    blocked: str | None,
    held: bool,
    last_entry_at: datetime | None,
    last_stop_at: datetime | None,
    now: datetime,
    risk: RiskConfig,
) -> str | None:
    """Why a new entry is not allowed right now, or None."""
    if blocked is not None:
        return blocked
    if held:
        return "already_held"
    if last_stop_at is not None and now - last_stop_at < timedelta(hours=risk.cooldown_hours):
        return "cooldown_after_stop"
    if last_entry_at is not None and now - last_entry_at < timedelta(
        hours=risk.entry_interval_hours
    ):
        return "entry_interval"
    return None
