"""GO / EXTEND / STOP from fixed, named thresholds. Pure: the same numbers, the same answer.

The thresholds are the assistant's proposal (DEC-049) and wait for the owner's approval. GO only
means "worth proposing a small real-money pilot for a separate decision", never approval to trade.
Returns are compared in percent of each strategy's own starting equity, because the strategies
entered the common window at slightly different values.
"""

from __future__ import annotations

from decimal import Decimal

from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.report.models import PairedResult, StrategyResult, Verdict

MIN_WINDOW_DAYS = Decimal(7)  # a verdict needs at least a week of paired time
MIN_CLOSED_TRADES = 5  # filled sells by the AI strategy inside the window
MIN_COVERAGE = Decimal(
    "0.90"
)  # share of expected 15-minute cycles that ran, for the worst strategy
MARGIN_POINTS = Decimal(1)  # percentage points the AI must beat buy-and-hold by, after its bill
DRAWDOWN_LIMIT = LiveConfig().risk.drawdown_fraction  # the strategies' own limit, not a copy


def _return_pct(s: StrategyResult) -> Decimal | None:
    if s.pnl is None or not s.start_equity:
        return None
    return s.pnl / s.start_equity * 100


def edge_over(p: PairedResult | None) -> bool:
    """A weak guard against a lucky day: ahead on most days, and the average gap beyond one
    standard error. It is not a significance test."""
    return (
        p is not None
        and p.days > 0
        and p.better_days * 2 > p.days
        and p.std_error is not None
        and p.mean_diff > p.std_error
    )


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
    paired_vs_benchmark: PairedResult | None,
    stale: bool = False,
    truncated: bool = False,
) -> Verdict:
    ai_pct = _return_pct(ai) if ai is not None else None
    bench_pct = _return_pct(benchmark) if benchmark is not None else None
    if ai is None or ai_pct is None:
        return Verdict(state="EXTEND", reasons=[], missing=["no_ai_data"])
    if benchmark is None or bench_pct is None:
        return Verdict(state="EXTEND", reasons=[], missing=["no_benchmark_data"])
    # A breach of the strategy's own risk limit is a stop whatever else is missing.
    if ai.drawdown_latched or ai.max_drawdown >= DRAWDOWN_LIMIT:
        return Verdict(state="STOP", reasons=["risk_limit_breached"], missing=[])
    missing: list[str] = []
    if window_days < MIN_WINDOW_DAYS:
        missing.append("window_under_7_days")
    if coverage < MIN_COVERAGE:
        missing.append("loop_coverage_under_90_percent")
    if stale:
        missing.append("data_not_current")
    if truncated:
        missing.append("history_truncated")
    if unacknowledged_errors:
        missing.append("unacknowledged_errors")
    if switches_in_window:
        missing.append("strategy_switched_in_window")
    if ai.closed_trades < MIN_CLOSED_TRADES:
        missing.append("too_few_closed_trades")
    if missing:
        return Verdict(state="EXTEND", reasons=[], missing=missing)
    start = ai.start_equity or Decimal(1)
    net = ai_pct - (ai_cost_eur / start * 100)
    if net <= 0 and net < bench_pct - MARGIN_POINTS:
        return Verdict(state="STOP", reasons=["loses_money_and_trails_buy_and_hold"], missing=[])
    rules_pct = _return_pct(rules) if rules is not None else None
    beats_benchmark = net >= bench_pct + MARGIN_POINTS
    beats_rules = rules_pct is None or net >= rules_pct
    edge = edge_over(paired_vs_benchmark)
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
        why.append("no_steady_daily_edge_over_buy_and_hold")
    return Verdict(state="EXTEND", reasons=why, missing=[])
