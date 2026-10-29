"""Pure functions behind the daily review: no I/O, no clock, no model.

Everything is derived from rows the loop already wrote. The observations are fixed rules with
named thresholds; their suggestions are advice for a human and are never applied by code.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from ai_trading_lab.ai.budget import CallFact
from ai_trading_lab.live.store import DecisionRow, EquityPoint
from ai_trading_lab.review.models import AIDay, DailyReview, Observation, StrategyDay

CYCLES_PER_DAY = 96  # one every 15 minutes
TOP_HOLDS = 5
TRAILING_DAYS = 7

NO_ENTRIES_AFTER_DAYS = 3  # a strategy that has not entered for this long is "slow"
AI_ABSTAIN_MIN_CALLS = 12
AI_ABSTAIN_SHARE = Decimal("0.95")
DATA_GAP_SHARE = Decimal("0.90")  # fewer cycles than this share of the day is a gap
FEE_DRAG_SHARE = Decimal("0.005")  # fees above 0.5% of the starting equity in one day
AI_PACE_SHARE = Decimal("0.5")  # projected spend over two weeks above half of the cap

LABELS = {"benchmark": "Buy and hold", "rules": "Rules breakout", "ai": "AI"}
ORDER = {"rules": 0, "ai": 1, "benchmark": 2}


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def expected_cycles(first_seen: datetime | None, start: datetime, end: datetime) -> int:
    """How many 15-minute cycles a full day holds; fewer on the day the loop first started."""
    if first_seen is None or first_seen <= start:
        return CYCLES_PER_DAY
    return max(1, int((end - first_seen).total_seconds() // 900))


def pick_equity(
    points: Sequence[EquityPoint], start: datetime, end: datetime
) -> tuple[Decimal | None, Decimal | None]:
    """Equity at the start and at the end of the window.

    The start is the last point at or before the window start; on the first day there is none, so
    the first point inside the window stands in. The end is the last point before the window end.
    """
    before = [p for p in points if p.at_utc <= start]
    inside = [p for p in points if start <= p.at_utc < end]
    first = before[-1] if before else (inside[0] if inside else None)
    last = inside[-1] if inside else (before[-1] if before else None)
    return (first.equity if first else None, last.equity if last else None)


def summarise_strategy(
    *,
    strategy_id: str,
    kind: str,
    points: Sequence[EquityPoint],
    decisions: Sequence[DecisionRow],
    start: datetime,
    end: datetime,
) -> StrategyDay:
    first, last = pick_equity(points, start, end)
    trades = [d for d in decisions if d.status in ("filled", "partial") and d.action != "hold"]
    holds = Counter(d.reason for d in decisions if d.action == "hold")
    fees = sum((_decimal((d.result or {}).get("fee", 0)) for d in trades), Decimal(0))
    notional = sum((_decimal((d.result or {}).get("notional", 0)) for d in trades), Decimal(0))
    return StrategyDay(
        strategy_id=strategy_id,
        kind=kind,
        label=LABELS.get(kind, kind),
        equity_start=first,
        equity_end=last,
        change=(last - first) if first is not None and last is not None else None,
        decisions=len(decisions),
        trades=len(trades),
        buys=sum(1 for d in trades if d.action == "buy"),
        sells=sum(1 for d in trades if d.action == "sell"),
        fees=fees,
        notional=notional,
        top_holds=dict(holds.most_common(TOP_HOLDS)),
    )


def summarise_ai(
    *,
    active: bool,
    calls: Sequence[CallFact],
    decisions: Sequence[DecisionRow],
    total_spent: Decimal,
    total_limit: Decimal,
) -> AIDay:
    """`calls` are the budget guard's per-call facts for the day: status, amount, held."""
    spent = sum((c.amount for c in calls), Decimal(0))
    held = sum((c.held for c in calls), Decimal(0))
    count = Counter(c.status for c in calls)
    return AIDay(
        active=active,
        calls=len(calls),
        completed=count["completed"],
        refused=count["refused"],
        invalid=count["invalid"],
        unresolved=count["settled"] + count[None],
        abstains=sum(1 for d in decisions if d.reason == "ai_abstain"),
        spent_usd=spent,
        held_usd=held,
        total_spent_usd=total_spent,
        total_limit_usd=total_limit,
    )


@dataclass(frozen=True)
class Trailing:
    """What the last week looked like, for the observations that need more than one day."""

    run_days: Decimal  # how long the strategies have been running
    entries: dict[str, int]  # strategy_id -> filled buys in the trailing window
    ai_calls: int
    ai_abstains: int


def observe(
    *,
    strategies: Sequence[StrategyDay],
    ai: AIDay,
    cycles_seen: int,
    cycles_expected: int,
    trailing: Trailing,
) -> list[Observation]:
    found: list[Observation] = []
    if cycles_expected and Decimal(cycles_seen) < DATA_GAP_SHARE * cycles_expected:
        found.append(
            Observation(
                code="data_gap",
                severity="watch",
                strategy_id=None,
                text=f"Only {cycles_seen} of {cycles_expected} cycles ran. The loop had gaps.",
            )
        )
    for s in strategies:
        if s.kind == "rules" and trailing.run_days >= NO_ENTRIES_AFTER_DAYS:
            if trailing.entries.get(s.strategy_id, 0) == 0:
                found.append(
                    Observation(
                        code="no_entries",
                        severity="watch",
                        strategy_id=s.strategy_id,
                        text=(
                            f"{s.label} has not entered a position in the last "
                            f"{min(int(trailing.run_days), TRAILING_DAYS)} days."
                        ),
                        suggestion=(
                            "Its entry rule may be too slow for this market. Consider a faster "
                            "variant as a NEW strategy version (never change this one in place); "
                            "it needs your approval and a recorded decision."
                        ),
                    )
                )
        if s.kind != "benchmark" and s.trades and s.equity_start:
            if s.fees >= FEE_DRAG_SHARE * s.equity_start:
                found.append(
                    Observation(
                        code="fee_drag",
                        severity="info",
                        strategy_id=s.strategy_id,
                        text=(
                            f"{s.label} paid EUR {s.fees:.2f} in fees on {s.trades} trades "
                            f"({(s.fees / s.equity_start * 100):.2f}% of its capital)."
                        ),
                    )
                )
    if ai.active:
        if ai.invalid or ai.unresolved:
            found.append(
                Observation(
                    code="ai_replies_unusable",
                    severity="watch",
                    strategy_id=None,
                    text=(
                        f"{ai.invalid} AI replies were unusable and {ai.unresolved} calls had "
                        "an unknown outcome today."
                    ),
                    suggestion=(
                        "Check the Errors tab. A reasoning model can use its whole output "
                        "allowance, or the API can answer with a different model name. Raising "
                        "the allowance is a new configuration version, not an in-place change."
                    ),
                )
            )
        if (
            trailing.ai_calls >= AI_ABSTAIN_MIN_CALLS
            and Decimal(trailing.ai_abstains) >= AI_ABSTAIN_SHARE * trailing.ai_calls
        ):
            found.append(
                Observation(
                    code="ai_always_abstains",
                    severity="info",
                    strategy_id=None,
                    text=(
                        f"The AI abstained on {trailing.ai_abstains} of its last "
                        f"{trailing.ai_calls} reviews."
                    ),
                    suggestion=(
                        "Either the market gave it nothing to do or its prompt or evidence is "
                        "too cautious. Review the prompt and evidence as a NEW version if this "
                        "continues; the answer does not change the risk rules."
                    ),
                )
            )
        if trailing.run_days > 0 and ai.total_limit_usd > 0:
            # Never extrapolate from less than a day: the first hours would look like a heavy pace.
            projected = ai.total_spent_usd / max(trailing.run_days, Decimal(1)) * 14
            if projected >= AI_PACE_SHARE * ai.total_limit_usd:
                found.append(
                    Observation(
                        code="ai_pace",
                        severity="watch",
                        strategy_id=None,
                        text=(
                            f"At the current pace the AI would spend about USD {projected:.2f} "
                            f"over two weeks (cap USD {ai.total_limit_usd})."
                        ),
                    )
                )
    return found


def render_text(review: DailyReview, pending_suggestions: int) -> str:
    """Plain text for the chat message: short, no markup, no secrets."""
    lines = [
        f"Daily review {review.day.isoformat()} (UTC). Paper only.",
        f"Loop: {review.cycles_seen}/{review.cycles_expected} cycles. "
        f"Errors in the 24 h before this was written: {review.errors_24h}, warnings "
        f"{review.warnings_24h}, unacknowledged {review.unacknowledged_errors}.",
    ]
    for s in sorted(review.strategies, key=lambda x: (ORDER.get(x.kind, 9), x.strategy_id)):
        if s.equity_end is None:
            lines.append(f"{s.label}: no data.")
            continue
        change = f"{s.change:+.2f}" if s.change is not None else "n/a"
        lines.append(
            f"{s.label}: EUR {s.equity_end:.2f} ({change} today), {s.trades} trades, "
            f"fees EUR {s.fees:.2f}."
        )
    if review.ai.active:
        lines.append(
            f"AI: {review.ai.calls} calls, {review.ai.abstains} abstained, "
            f"{review.ai.invalid} unusable; spent about USD {review.ai.spent_usd:.3f} today "
            f"(USD {review.ai.total_spent_usd:.2f} of {review.ai.total_limit_usd} in total)."
        )
    else:
        lines.append("AI: off.")
    for o in review.observations:
        lines.append(("Watch: " if o.severity == "watch" else "Note: ") + o.text)
    if pending_suggestions:
        lines.append(
            f"{pending_suggestions} suggestion(s) wait for your decision: "
            "docker compose exec alerts /app/.venv/bin/atl-review suggestions"
        )
    return "\n".join(lines)[:3000]


def window_for(day_start: datetime) -> tuple[datetime, datetime]:
    return day_start, day_start + timedelta(days=1)
