"""Series arithmetic of the evaluation report."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ai_trading_lab.live.store import EquityPoint
from ai_trading_lab.report import metrics

D = Decimal
T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def pts(
    *values: str, start: datetime = T0, step: timedelta = timedelta(hours=6)
) -> list[EquityPoint]:
    return [
        EquityPoint(at_utc=start + step * i, equity=D(v), cash=D(0), positions_value=D(0))
        for i, v in enumerate(values)
    ]


def test_the_common_window_starts_when_the_youngest_strategy_started() -> None:
    old = pts("100", "99", "98", "97", "96", "95", start=T0 - timedelta(hours=6))
    young = pts("100", "101", "102", start=T0)
    window = metrics.common_window({"rules": old, "ai": young})
    assert window is not None
    assert window.start == T0 and window.end == T0 + timedelta(hours=12)  # the young one ends first
    assert window.days == D("0.5")


def test_no_common_window_without_data_or_overlap() -> None:
    assert metrics.common_window({}) is None
    assert metrics.common_window({"a": pts("100"), "b": []}) is None
    early = pts("100", "99", start=T0 - timedelta(days=5))
    late = pts("100", "99", start=T0)
    assert metrics.common_window({"a": early, "b": late}) is None  # they never overlap


def test_since_and_until_narrow_the_window() -> None:
    a = pts("100", "101", "102", "103", "104")
    window = metrics.common_window(
        {"a": a}, since=T0 + timedelta(hours=6), until=T0 + timedelta(hours=18)
    )
    assert window is not None and metrics.pnl(metrics.clip(a, window)) == D("2")
    assert metrics.common_window({"a": a}, since=T0 + timedelta(days=9)) is None


def test_pnl_needs_two_points() -> None:
    assert metrics.pnl(pts("100")) is None
    assert metrics.pnl(pts("100", "97.5")) == D("-2.5")


def test_max_drawdown_is_the_worst_peak_to_trough_fall() -> None:
    assert metrics.max_drawdown(pts("100", "110", "99", "105", "104")) == D("0.1")
    assert metrics.max_drawdown(pts("100", "101", "102")) == D("0")
    assert metrics.max_drawdown([]) == D("0")


def test_daily_changes_chain_from_the_previous_days_close() -> None:
    points = pts("100", "101", "103", "102", "108", step=timedelta(hours=9))
    # T0 12:00 d1 (100), 21:00 d1 (101), 06:00 d2 (103), 15:00 d2 (102), 00:00 d3 (108)
    changes = metrics.daily_changes(points)
    assert changes == {
        date(2026, 10, 29): D("1"),
        date(2026, 10, 30): D("1"),
        date(2026, 10, 31): D("6"),
    }


def test_paired_counts_better_days_and_estimates_the_error_only_from_three_days() -> None:
    a = {date(2026, 10, d): D(x) for d, x in ((1, "1"), (2, "-1"), (3, "2"), (4, "1"))}
    b = {date(2026, 10, d): D(x) for d, x in ((1, "0"), (2, "0"), (3, "0"), (4, "0"), (5, "9"))}
    result = metrics.paired(a, b)
    assert (result.days, result.better_days) == (4, 3)  # day 5 is not shared
    assert result.mean_diff == D("0.75") and result.std_error is not None
    few = metrics.paired({date(2026, 10, 1): D(1)}, {date(2026, 10, 1): D(0)})
    assert few.days == 1 and few.std_error is None
    assert metrics.paired({}, {}) == metrics.Paired(0, 0, D(0), None)
