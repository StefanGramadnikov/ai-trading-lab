"""The daily review's pure functions: windows, trade and fee counts, observations, the message."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from ai_trading_lab.ai.budget import CallFact
from ai_trading_lab.live.store import DecisionRow, EquityPoint
from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.review import digest
from ai_trading_lab.review.models import AIDay, DailyReview, StrategyDay

D = Decimal
START = datetime(2026, 10, 30, tzinfo=UTC)
END = START + timedelta(days=1)


def point(at: datetime, equity: str) -> EquityPoint:
    return EquityPoint(at_utc=at, equity=D(equity), cash=D(0), positions_value=D(0))


def decision(
    action: str,
    status: str,
    reason: str = "r",
    *,
    fee: str | None = None,
    notional: str | None = None,
) -> DecisionRow:
    result = {"fee": fee, "notional": notional} if fee is not None else None
    return DecisionRow(
        decision_id=uuid4(),
        strategy_id="s",
        pair="XBTEUR",
        cycle_at_utc=START + timedelta(hours=1),
        action=action,
        reason=reason,
        status=status,
        result=result,
    )


def day_of(kind: str = "rules", **changes: object) -> StrategyDay:
    base: dict[str, object] = {
        "strategy_id": f"v0-{kind}",
        "kind": kind,
        "label": digest.LABELS[kind],
        "equity_start": D("100"),
        "equity_end": D("100"),
        "change": D("0"),
        "decisions": 288,
        "trades": 0,
        "buys": 0,
        "sells": 0,
        "fees": D("0"),
        "notional": D("0"),
        "top_holds": {"no_breakout": 288},
    }
    return StrategyDay.model_validate(base | changes)


def ai_day(**changes: object) -> AIDay:
    base: dict[str, object] = {
        "active": True,
        "calls": 18,
        "completed": 18,
        "refused": 0,
        "invalid": 0,
        "unresolved": 0,
        "abstains": 18,
        "spent_usd": D("0.045"),
        "held_usd": D(0),
        "total_spent_usd": D("0.09"),
        "total_limit_usd": D("15"),
    }
    return AIDay.model_validate(base | changes)


def trailing(**changes: object) -> digest.Trailing:
    base: dict[str, object] = {
        "run_days": D("5"),
        "entries": {"v0-rules": 1},
        "ai_calls": 0,
        "ai_abstains": 0,
    }
    return digest.Trailing(**(base | changes))  # type: ignore[arg-type]


def test_equity_starts_from_the_last_point_before_the_window_and_ends_inside_it() -> None:
    points = [
        point(START - timedelta(hours=2), "99"),
        point(START - timedelta(minutes=15), "100"),
        point(START + timedelta(hours=3), "101"),
        point(END - timedelta(minutes=15), "102"),
        point(END + timedelta(minutes=15), "150"),  # tomorrow: never counted today
    ]
    assert digest.pick_equity(points, START, END) == (D("100"), D("102"))


def test_the_first_day_starts_from_its_first_point_and_a_day_without_data_has_none() -> None:
    first_day = [point(START + timedelta(hours=1), "100"), point(START + timedelta(hours=2), "98")]
    assert digest.pick_equity(first_day, START, END) == (D("100"), D("98"))
    assert digest.pick_equity([], START, END) == (None, None)
    quiet = [point(START - timedelta(days=1), "97")]
    assert digest.pick_equity(quiet, START, END) == (D("97"), D("97"))  # carried over


def test_trades_fees_and_holds_are_counted_from_filled_decisions_only() -> None:
    rows = [
        decision("buy", "filled", fee="0.16", notional="20"),
        decision("sell", "partial", fee="0.08", notional="10"),
        decision("buy", "rejected"),  # never traded: not a trade
        decision("hold", "skipped", "no_breakout"),
        decision("hold", "skipped", "no_breakout"),
        decision("hold", "skipped", "holding"),
    ]
    day = digest.summarise_strategy(
        strategy_id="v0-rules",
        kind="rules",
        points=[point(START, "100"), point(END - timedelta(minutes=15), "99.5")],
        decisions=rows,
        start=START,
        end=END,
    )
    assert (day.trades, day.buys, day.sells, day.decisions) == (2, 1, 1, 6)
    assert (day.fees, day.notional) == (D("0.24"), D("30"))
    assert day.top_holds == {"no_breakout": 2, "holding": 1}
    assert day.change == D("-0.5") and day.label == "Rules breakout"


def test_ai_calls_are_split_by_outcome_and_unknown_ones_count_as_unresolved() -> None:
    at = START + timedelta(hours=1)
    calls = [
        CallFact(at, "completed", D("0.002"), D(0)),
        CallFact(at, "completed", D("0.003"), D(0)),
        CallFact(at, "invalid", D("0.004"), D(0)),
        # a call settled at its maximum is counted as spent, as BudgetStore does
        CallFact(at, "settled", D("0.03"), D(0)),
        CallFact(at, None, D(0), D("0.03")),  # reserved, never finished: still held
    ]
    abstains = [decision("hold", "skipped", "ai_abstain") for _ in range(3)]
    ai = digest.summarise_ai(
        active=True, calls=calls, decisions=abstains, total_spent=D("0.2"), total_limit=D("15")
    )
    assert (ai.calls, ai.completed, ai.invalid, ai.unresolved, ai.abstains) == (5, 2, 1, 2, 3)
    assert ai.spent_usd == D("0.039") and ai.held_usd == D("0.03")


def test_a_day_the_loop_started_late_expects_fewer_cycles() -> None:
    assert digest.expected_cycles(None, START, END) == 96
    assert digest.expected_cycles(START - timedelta(days=3), START, END) == 96
    assert digest.expected_cycles(END - timedelta(hours=1), START, END) == 4
    assert digest.expected_cycles(END - timedelta(seconds=5), START, END) == 1


def test_the_spend_pace_is_never_extrapolated_from_less_than_a_day() -> None:
    first_hours = ai_day(total_spent_usd=D("0.05"))  # USD 0.05 after one hour
    assert "ai_pace" not in codes(ai=first_hours, trailing=trailing(run_days=D("0.04")))


def codes(**kw: object) -> set[str]:
    args: dict[str, object] = {
        "strategies": [day_of("rules"), day_of("ai", strategy_id="v0-ai"), day_of("benchmark")],
        "ai": ai_day(),
        "cycles_seen": 96,
        "cycles_expected": 96,
        "trailing": trailing(),
    }
    return {o.code for o in digest.observe(**(args | kw))}  # type: ignore[arg-type]


def test_a_quiet_healthy_day_raises_nothing() -> None:
    assert codes() == set()


def test_a_rules_strategy_that_has_not_entered_for_days_is_flagged_as_slow() -> None:
    assert "no_entries" in codes(trailing=trailing(entries={"v0-rules": 0}))
    # too young to judge, or it has entered: nothing
    assert "no_entries" not in codes(trailing=trailing(entries={"v0-rules": 0}, run_days=D("2")))
    found = digest.observe(
        strategies=[day_of("rules")],
        ai=ai_day(active=False),
        cycles_seen=96,
        cycles_expected=96,
        trailing=trailing(entries={"v0-rules": 0}),
    )
    assert "NEW strategy version" in (found[0].suggestion or "")


def test_missing_cycles_are_a_data_gap() -> None:
    assert "data_gap" in codes(cycles_seen=40)
    assert "data_gap" not in codes(cycles_seen=90)


def test_unusable_ai_replies_and_unknown_outcomes_are_flagged() -> None:
    assert "ai_replies_unusable" in codes(ai=ai_day(invalid=1))
    assert "ai_replies_unusable" in codes(ai=ai_day(unresolved=2))
    assert "ai_replies_unusable" not in codes(ai=ai_day(active=False, invalid=5))


def test_an_ai_that_always_abstains_is_noted_only_with_enough_reviews() -> None:
    assert "ai_always_abstains" in codes(trailing=trailing(ai_calls=18, ai_abstains=18))
    assert "ai_always_abstains" not in codes(trailing=trailing(ai_calls=8, ai_abstains=8))
    assert "ai_always_abstains" not in codes(trailing=trailing(ai_calls=18, ai_abstains=10))


def test_ai_spend_pace_is_projected_over_two_weeks() -> None:
    heavy = ai_day(total_spent_usd=D("5"))  # USD 1/day for 5 days: 14 days would be USD 14
    assert "ai_pace" in codes(ai=heavy)
    assert "ai_pace" not in codes(ai=ai_day(total_spent_usd=D("0.09")))


def test_fee_drag_is_noted_for_traders_but_never_for_the_benchmark() -> None:
    busy = day_of("rules", trades=4, fees=D("0.8"), change=D("-0.9"))
    assert "fee_drag" in codes(strategies=[busy])
    assert "fee_drag" not in codes(
        strategies=[day_of("benchmark", trades=3, fees=D("0.8"), change=D("-0.9"))]
    )


def review(**changes: object) -> DailyReview:
    base: dict[str, object] = {
        "day": date(2026, 10, 30),
        "generated_at": END + timedelta(minutes=40),
        "window_start": START,
        "window_end": END,
        "cycles_seen": 96,
        "cycles_expected": 96,
        "errors_24h": 0,
        "warnings_24h": 2,
        "unacknowledged_errors": 0,
        "strategies": [
            day_of("rules", change=D("0.5"), equity_end=D("100.5"), trades=1, fees=D("0.16")),
            day_of("ai", strategy_id="v0-ai"),
            day_of("benchmark", equity_end=D("97.8"), change=D("-0.7")),
        ],
        "ai": ai_day(),
        "observations": [],
    }
    return DailyReview.model_validate(base | changes)


def test_the_message_is_short_plain_text_the_outbox_accepts() -> None:
    text = digest.render_text(review(), pending_suggestions=2)
    assert text.startswith("Daily review 2026-10-30 (UTC). Paper only.")
    assert "Rules breakout: EUR 100.50 (+0.50 today), 1 trades, fees EUR 0.16." in text
    assert "Buy and hold: EUR 97.80 (-0.70 today)" in text
    assert "AI: 18 calls, 18 abstained" in text and "2 suggestion(s) wait" in text
    assert len(text) <= 3000
    Notification(
        event_key="review:2026-10-30", kind="daily_summary", occurred_at_utc=END, text=text
    )  # validation: no secrets, no control characters


def test_the_message_says_so_when_the_ai_is_off_or_a_strategy_has_no_data() -> None:
    quiet = review(
        ai=ai_day(active=False),
        strategies=[day_of("rules", equity_start=None, equity_end=None, change=None)],
    )
    text = digest.render_text(quiet, pending_suggestions=0)
    assert "AI: off." in text and "Rules breakout: no data." in text
    assert "suggestion" not in text
