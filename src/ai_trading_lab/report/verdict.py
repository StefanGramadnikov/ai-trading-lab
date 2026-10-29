"""GO / EXTEND / STOP from fixed, named thresholds. Pure: the same numbers, the same answer.

The thresholds are the assistant's proposal (DEC-049) and wait for the owner's approval. GO only
means "worth proposing a small real-money pilot for a separate decision", never approval to trade.
"""

from __future__ import annotations

from decimal import Decimal

from ai_trading_lab.report.models import StrategyResult, Verdict

MIN_WINDOW_DAYS = Decimal(7)  # a verdict needs at least a week of paired time
MIN_CLOSED_TRADES = 5  # closed round trips by the AI strategy inside the window
MIN_COVERAGE = Decimal("0.90")  # share of expected 15-minute cycles that ran
MARGIN_EUR = Decimal(1)  # the AI must beat buy-and-hold by this much (after costs) to count
DRAWDOWN_LIMIT = Decimal("0.10")  # the risk limit of the strategies themselves (RiskConfig)


def decide(
    *,
    ai: StrategyResult | None,
    rules: StrategyResult | None,
    benchmark: StrategyResult | None,
    ai_cost_eur: Decimal,
    window_days: Decimal,
    coverage: Decimal,
    unacknowledged_errors: int,
    switches_in_window: int,
    paired_mean_vs_benchmark: Decimal | None,
) -> Verdict:
    missing: list[str] = []
    if ai is None or ai.pnl is None:
        return Verdict(state="EXTEND", reasons=[], missing=["no_ai_data"])
    if benchmark is None or benchmark.pnl is None:
        return Verdict(state="EXTEND", reasons=[], missing=["no_benchmark_data"])
    if window_days < MIN_WINDOW_DAYS:
        missing.append("window_under_7_days")
    if coverage < MIN_COVERAGE:
        missing.append("loop_coverage_under_90_percent")
    if unacknowledged_errors:
        missing.append("unacknowledged_errors")
    if switches_in_window:
        missing.append("strategy_switched_in_window")
    if ai.closed_trades < MIN_CLOSED_TRADES:
        missing.append("too_few_closed_trades")
    if missing:
        return Verdict(state="EXTEND", reasons=[], missing=missing)
    net = ai.pnl - ai_cost_eur
    # A risk breach is a stop whatever the return was.
    if ai.drawdown_latched or ai.max_drawdown >= DRAWDOWN_LIMIT:
        return Verdict(state="STOP", reasons=["risk_limit_breached"], missing=[])
    if net <= 0 and net < benchmark.pnl - MARGIN_EUR:
        return Verdict(state="STOP", reasons=["loses_money_and_trails_buy_and_hold"], missing=[])
    beats_benchmark = net >= benchmark.pnl + MARGIN_EUR
    beats_rules = rules is None or rules.pnl is None or net >= rules.pnl
    edge = paired_mean_vs_benchmark is not None and paired_mean_vs_benchmark > 0
    if net > 0 and beats_benchmark and beats_rules and edge:
        return Verdict(
            state="GO",
            reasons=["positive_after_costs", "beats_buy_and_hold", "beats_rules"],
            missing=[],
        )
    why = []
    if net <= 0:
        why.append("not_profitable_after_costs")
    if not beats_benchmark:
        why.append("not_clearly_better_than_buy_and_hold")
    if not beats_rules:
        why.append("trails_the_rules_strategy")
    if not edge:
        why.append("no_daily_edge_over_buy_and_hold")
    return Verdict(state="EXTEND", reasons=why, missing=[])
