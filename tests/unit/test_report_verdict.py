"""GO / EXTEND / STOP from the named thresholds."""

from decimal import Decimal

import pytest

from ai_trading_lab.report import verdict
from ai_trading_lab.report.models import StrategyResult, Verdict

D = Decimal


def result(kind: str, pnl: str | None, **changes: object) -> StrategyResult:
    base: dict[str, object] = {
        "strategy_id": f"v0-{kind}",
        "kind": kind,
        "label": kind,
        "start_equity": D("100"),
        "end_equity": D("100") + D(pnl) if pnl is not None else None,
        "pnl": D(pnl) if pnl is not None else None,
        "pnl_pct": None,
        "max_drawdown": D("0.02"),
        "entries": 6,
        "closed_trades": 6,
        "fees": D("1"),
        "turnover": D("120"),
        "drawdown_latched": False,
    }
    return StrategyResult.model_validate(base | changes)


def decide(**changes: object) -> Verdict:
    base: dict[str, object] = {
        "ai": result("ai", "4"),
        "rules": result("rules", "1"),
        "benchmark": result("benchmark", "1"),
        "ai_cost_eur": D("0.5"),
        "window_days": D("10"),
        "coverage": D("0.99"),
        "unacknowledged_errors": 0,
        "switches_in_window": 0,
        "paired_mean_vs_benchmark": D("0.3"),
    }
    return verdict.decide(**(base | changes))  # type: ignore[arg-type]


def test_a_clear_edge_after_costs_over_enough_time_and_trades_is_go() -> None:
    v = decide()
    assert v.state == "GO" and v.missing == []
    assert "beats_buy_and_hold" in v.reasons


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"window_days": D("3")}, "window_under_7_days"),
        ({"coverage": D("0.8")}, "loop_coverage_under_90_percent"),
        ({"unacknowledged_errors": 1}, "unacknowledged_errors"),
        ({"switches_in_window": 2}, "strategy_switched_in_window"),
        ({"ai": result("ai", "4", closed_trades=2)}, "too_few_closed_trades"),
    ],
)
def test_anything_that_makes_the_sample_untrustworthy_extends_the_run(
    changes: dict[str, object], missing: str
) -> None:
    v = decide(**changes)
    assert v.state == "EXTEND" and missing in v.missing


def test_missing_ai_or_benchmark_data_extends() -> None:
    assert decide(ai=None).missing == ["no_ai_data"]
    assert decide(ai=result("ai", None)).missing == ["no_ai_data"]
    assert decide(benchmark=None).missing == ["no_benchmark_data"]


def test_the_ai_bill_counts_against_it() -> None:
    # EUR 2.5 on paper, but a bill of EUR 3 makes it a loss
    v = decide(ai=result("ai", "2.5"), ai_cost_eur=D("3"), benchmark=result("benchmark", "0"))
    assert v.state == "EXTEND" and "not_profitable_after_costs" in v.reasons


def test_a_loss_that_also_trails_buy_and_hold_is_stop() -> None:
    v = decide(ai=result("ai", "-3"), benchmark=result("benchmark", "0.5"))
    assert v.state == "STOP" and v.reasons == ["loses_money_and_trails_buy_and_hold"]
    # losing money in a falling market while buy-and-hold lost more is not a stop
    assert decide(ai=result("ai", "-3"), benchmark=result("benchmark", "-8")).state == "EXTEND"


def test_a_risk_breach_is_stop_even_with_a_profit() -> None:
    assert decide(ai=result("ai", "6", max_drawdown=D("0.11"))).reasons == ["risk_limit_breached"]
    assert decide(ai=result("ai", "6", drawdown_latched=True)).state == "STOP"


def test_a_profit_that_does_not_beat_the_rules_or_the_benchmark_by_the_margin_extends() -> None:
    assert "trails_the_rules_strategy" in decide(rules=result("rules", "9")).reasons
    close = decide(ai=result("ai", "1.5"), benchmark=result("benchmark", "1"))
    assert close.state == "EXTEND" and "not_clearly_better_than_buy_and_hold" in close.reasons
    no_edge = decide(paired_mean_vs_benchmark=D("-0.1"))
    assert no_edge.state == "EXTEND" and "no_daily_edge_over_buy_and_hold" in no_edge.reasons
    assert decide(paired_mean_vs_benchmark=None).state == "EXTEND"
