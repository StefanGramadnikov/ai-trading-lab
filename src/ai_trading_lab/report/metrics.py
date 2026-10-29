"""Pure series arithmetic for the evaluation report: no I/O, no clock, no model.

Money is `Decimal`. Every figure is read from equity points the loop recorded after fees and
spread, so a P&L here is already "after trading costs"; only the AI provider's bill sits outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import sqrt

from ai_trading_lab.live.store import EquityPoint

ZERO = Decimal(0)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def days(self) -> Decimal:
        return Decimal(max((self.end - self.start).total_seconds(), 0)) / Decimal(86400)


def common_window(
    series: Mapping[str, Sequence[EquityPoint]],
    since: datetime | None = None,
    until: datetime | None = None,
) -> Window | None:
    """The span in which every strategy has data: it starts when the youngest one started.

    A paired comparison is only fair over the same time, so a strategy that was switched on later
    (the AI joined a day after the others) sets the start for everyone.
    """
    if not series or any(not points for points in series.values()):
        return None
    start = max(points[0].at_utc for points in series.values())
    end = min(points[-1].at_utc for points in series.values())
    if since is not None:
        start = max(start, since)
    if until is not None:
        end = min(end, until)
    return Window(start, end) if end > start else None


def clip(points: Sequence[EquityPoint], window: Window) -> list[EquityPoint]:
    return [p for p in points if window.start <= p.at_utc <= window.end]


def pnl(points: Sequence[EquityPoint]) -> Decimal | None:
    """Equity at the end minus equity at the start of the (already clipped) points."""
    return points[-1].equity - points[0].equity if len(points) >= 2 else None


def max_drawdown(points: Sequence[EquityPoint]) -> Decimal:
    """The largest peak-to-trough fall as a fraction of the peak (0.05 is a 5% fall)."""
    peak, worst = ZERO, ZERO
    for p in points:
        peak = max(peak, p.equity)
        if peak > 0:
            worst = max(worst, (peak - p.equity) / peak)
    return worst


def daily_changes(points: Sequence[EquityPoint]) -> dict[date, Decimal]:
    """Change of equity per UTC day, from the last point of the previous day (or the first)."""
    result: dict[date, Decimal] = {}
    previous: Decimal | None = None
    by_day: dict[date, Decimal] = {}
    for p in points:
        by_day[p.at_utc.date()] = p.equity
    first = points[0].equity if points else ZERO
    for day in sorted(by_day):
        base = previous if previous is not None else first
        result[day] = by_day[day] - base
        previous = by_day[day]
    return result


@dataclass(frozen=True)
class Paired:
    """One strategy against another over the days both have: how often and by how much."""

    days: int
    better_days: int
    mean_diff: Decimal
    std_error: Decimal | None  # None below three days: there is nothing to estimate from


def paired(a: dict[date, Decimal], b: dict[date, Decimal]) -> Paired:
    shared = sorted(set(a) & set(b))
    diffs = [a[d] - b[d] for d in shared]
    n = len(diffs)
    if n == 0:
        return Paired(0, 0, ZERO, None)
    mean = sum(diffs, ZERO) / n
    error: Decimal | None = None
    if n >= 3:
        variance = sum(((x - mean) ** 2 for x in diffs), ZERO) / (n - 1)
        error = Decimal(str(sqrt(variance / n)))
    return Paired(n, sum(1 for x in diffs if x > 0), mean, error)
