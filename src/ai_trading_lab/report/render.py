"""The report as Markdown: one page a person can read in two minutes."""

from __future__ import annotations

from decimal import Decimal

from ai_trading_lab.report.models import EvaluationReport

WORDS = {
    "no_ai_data": "the AI strategy has no data yet",
    "no_benchmark_data": "the buy-and-hold benchmark has no data yet",
    "window_under_7_days": "less than 7 days of paired time",
    "loop_coverage_under_90_percent": "the loop missed more than 10% of its cycles",
    "unacknowledged_errors": "there are unacknowledged errors",
    "strategy_switched_in_window": "a strategy was switched on or off during the window",
    "too_few_closed_trades": "the AI strategy closed too few trades to judge",
    "risk_limit_breached": "the AI strategy hit its own risk limit",
    "loses_money_and_trails_buy_and_hold": "the AI loses money and trails buy and hold",
    "positive_after_costs": "positive after all costs",
    "beats_buy_and_hold": "beats buy and hold by the required margin",
    "beats_rules": "beats the rules strategy",
    "not_profitable_after_costs": "not profitable after the AI bill",
    "not_clearly_better_than_buy_and_hold": "not clearly better than buy and hold",
    "trails_the_rules_strategy": "trails the rules strategy",
    "no_daily_edge_over_buy_and_hold": "no average daily edge over buy and hold",
}


def _eur(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def markdown(report: EvaluationReport) -> str:
    v = report.verdict
    lines = [
        "# AI Trading Lab: evaluation report (paper only)",
        "",
        f"Generated {report.generated_at:%Y-%m-%d %H:%M} UTC.",
    ]
    if report.window_start and report.window_end:
        lines.append(
            f"Paired window: {report.window_start:%Y-%m-%d %H:%M} to "
            f"{report.window_end:%Y-%m-%d %H:%M} UTC ({report.window_days:.1f} days)."
        )
    else:
        lines.append("Paired window: none yet (not every strategy has data).")
    lines += ["", f"## Verdict: {v.state}", ""]
    if v.state == "GO":
        lines.append(
            "Worth proposing a small real-money pilot for a separate decision. "
            "This is not an approval."
        )
    elif v.state == "STOP":
        lines.append("The evidence says to stop this approach.")
    else:
        lines.append(
            "Not enough to decide. Keep running, or change the experiment as a new version."
        )
    for title, items in (("Why", v.reasons), ("What is missing", v.missing)):
        if items:
            lines += ["", f"{title}:"] + [f"- {WORDS.get(code, code)}" for code in items]
    lines += [
        "",
        "## Results over the window (after fees and spread)",
        "",
        "| Strategy | Start | End | P&L EUR | P&L % | Worst fall | Entries | Closed | Fees EUR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report.strategies:
        start = "n/a" if s.start_equity is None else f"{s.start_equity:.2f}"
        end = "n/a" if s.end_equity is None else f"{s.end_equity:.2f}"
        lines.append(
            f"| {s.label} | {start} | {end} | {_eur(s.pnl)} | {_pct(s.pnl_pct)} | "
            f"{s.max_drawdown * 100:.1f}% | {s.entries} | {s.closed_trades} | {s.fees:.2f} |"
        )
    ai = report.ai_cost
    lines += [
        "",
        "## AI cost",
        "",
        f"{ai.calls} calls in the window, about USD {ai.spent_usd:.3f} spent"
        f" (+ USD {ai.held_usd:.3f} held for unknown outcomes), counted as EUR {ai.cost_eur:.3f}"
        f" at 1 USD = {ai.usd_to_eur} EUR. These are usage estimates, not an invoice.",
    ]
    if report.ai_net_pnl is not None:
        lines.append(f"AI P&L after its own bill: EUR {_eur(report.ai_net_pnl)}.")
    if report.paired:
        lines += ["", "## Day by day (UTC days both strategies have)", ""]
        for p in report.paired:
            error = "n/a" if p.std_error is None else f"{p.std_error:.2f}"
            lines.append(
                f"- {p.label} against {p.versus}: ahead on {p.better_days} of {p.days} days, "
                f"average difference EUR {_eur(p.mean_diff)} per day (standard error {error})."
            )
    i = report.integrity
    lines += [
        "",
        "## Can the data be trusted?",
        "",
        f"- Loop coverage: {i.coverage * 100:.0f}% of expected cycles ran.",
        f"- Unacknowledged errors: {i.unacknowledged_errors} "
        f"(errors in the last 24 h: {i.errors_24h}).",
        f"- Strategy switches inside the window: {i.switches_in_window}.",
        "",
        "## Thresholds used (proposed, awaiting the owner's approval)",
        "",
    ]
    lines += [f"- {key.replace('_', ' ')}: {value}" for key, value in report.thresholds.items()]
    lines += ["", "## Assumptions and limits", ""] + [f"- {a}" for a in report.assumptions]
    return "\n".join(lines) + "\n"
