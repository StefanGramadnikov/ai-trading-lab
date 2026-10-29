"""Typed shape of the evaluation report. Money is `Decimal`; the report serialises to JSON."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from ai_trading_lab.market_data.models import FrozenModel


class StrategyResult(FrozenModel):
    strategy_id: str
    kind: str
    label: str
    start_equity: Decimal | None
    end_equity: Decimal | None
    pnl: Decimal | None  # end minus start over the common window, after fees and spread
    pnl_pct: Decimal | None
    max_drawdown: Decimal  # fraction of the peak, inside the window
    entries: int  # filled buys inside the window
    closed_trades: int  # filled sells inside the window
    fees: Decimal
    turnover: Decimal  # money bought and sold inside the window
    drawdown_latched: bool  # the strategy's own drawdown stop is on right now


class PairedResult(FrozenModel):
    """`label` against `versus` over the UTC days both have."""

    label: str
    versus: str
    days: int
    better_days: int
    mean_diff: Decimal
    std_error: Decimal | None


class AICost(FrozenModel):
    calls: int
    spent_usd: Decimal
    held_usd: Decimal
    usd_to_eur: Decimal
    cost_eur: Decimal  # spent plus held, converted: the provider's bill is outside the paper P&L


class Integrity(FrozenModel):
    coverage: Decimal  # 0..1, cycles that ran over cycles expected in the window
    unacknowledged_errors: int
    errors_24h: int
    switches_in_window: int
    stale: bool  # the newest equity point is older than two cycles (and no --until was given)
    truncated: bool  # more history exists than one report reads
    ignored_strategies: list[str]  # extra rows of a kind that already had a newer one


class Verdict(FrozenModel):
    state: Literal["GO", "EXTEND", "STOP"]
    reasons: list[str]
    missing: list[str]


class EvaluationReport(FrozenModel):
    generated_at: datetime
    window_start: datetime | None
    window_end: datetime | None
    window_days: Decimal
    strategies: list[StrategyResult]
    paired: list[PairedResult]
    ai_cost: AICost
    ai_net_pnl: Decimal | None  # the AI strategy's P&L minus the provider's bill
    integrity: Integrity
    verdict: Verdict
    thresholds: dict[str, str]
    assumptions: list[str]
