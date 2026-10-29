"""Typed shape of one daily review. Money is `Decimal`; the payload is stored as JSON."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from ai_trading_lab.market_data.models import FrozenModel


class StrategyDay(FrozenModel):
    strategy_id: str
    kind: str
    label: str
    equity_start: Decimal | None
    equity_end: Decimal | None
    change: Decimal | None
    decisions: int
    trades: int
    buys: int
    sells: int
    fees: Decimal
    notional: Decimal
    top_holds: dict[str, int]


class AIDay(FrozenModel):
    active: bool
    calls: int
    completed: int
    refused: int
    invalid: int
    unresolved: int
    abstains: int
    spent_usd: Decimal
    held_usd: Decimal
    total_spent_usd: Decimal
    total_limit_usd: Decimal


class Observation(FrozenModel):
    """Something worth the owner's attention. `suggestion` is advice for a human, never applied."""

    code: str
    severity: Literal["info", "watch"]
    strategy_id: str | None
    text: str
    suggestion: str | None = None


class DailyReview(FrozenModel):
    day: date
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    cycles_seen: int
    cycles_expected: int
    errors_24h: int
    warnings_24h: int
    unacknowledged_errors: int
    strategies: list[StrategyDay]
    ai: AIDay
    observations: list[Observation]
