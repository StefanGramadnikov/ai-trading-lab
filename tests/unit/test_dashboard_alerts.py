"""The alert rules the page shows and the Telegram sender will use (approved 2026-10-29)."""

from datetime import timedelta
from decimal import Decimal

from ai_trading_lab.dashboard.alerts import RULES, Facts, evaluate


def rules(facts: Facts) -> list[str]:
    return [f.rule for f in evaluate(facts)]


FRESH: dict[str, timedelta | None] = {
    "XBTEUR": timedelta(minutes=5),
    "ETHEUR": timedelta(minutes=5),
}


def test_a_healthy_system_raises_nothing() -> None:
    assert evaluate(Facts(loop_age=timedelta(minutes=10), price_age=FRESH)) == []


def test_a_run_with_no_data_yet_raises_nothing_about_the_loop() -> None:
    assert "R1" not in rules(Facts(loop_age=None))


def test_the_loop_is_stalled_only_after_forty_minutes() -> None:
    assert rules(Facts(loop_age=timedelta(minutes=40))) == []
    assert rules(Facts(loop_age=timedelta(minutes=41))) == ["R1"]


def test_a_strategy_needs_three_failed_cycles_to_count_as_stopped() -> None:
    assert rules(Facts(loop_age=timedelta(0), strategy_failures={"v0-ai": 2})) == []
    (finding,) = evaluate(Facts(loop_age=timedelta(0), strategy_failures={"v0-ai": 3}))
    assert (finding.rule, finding.strategy_id, finding.key) == ("R2", "v0-ai", "strategy:v0-ai")


def test_prices_are_stale_after_forty_five_minutes_or_when_never_seen() -> None:
    assert rules(Facts(loop_age=timedelta(0), price_age={"XBTEUR": timedelta(minutes=45)})) == []
    found = evaluate(
        Facts(
            loop_age=timedelta(0),
            price_age={"XBTEUR": timedelta(minutes=46), "SOLEUR": None},
        )
    )
    assert [(f.rule, f.key) for f in found] == [("R3", "prices:SOLEUR"), ("R3", "prices:XBTEUR")]
    assert "never arrived" in found[0].detail


def test_ai_rules_stay_silent_while_the_ai_is_off() -> None:
    facts = Facts(
        loop_age=timedelta(0),
        ai_active=False,
        ai_unknown_24h=9,
        ai_breaker_open=True,
        ai_budget_used=Decimal(2),
    )
    assert evaluate(facts) == []


def test_three_unknown_outcomes_warn_and_an_open_breaker_is_critical() -> None:
    base = {"loop_age": timedelta(0), "ai_active": True}
    assert rules(Facts(**base, ai_unknown_24h=2)) == []  # type: ignore[arg-type]
    (warn,) = evaluate(Facts(**base, ai_unknown_24h=3))  # type: ignore[arg-type]
    assert (warn.rule, warn.severity) == ("R4", "warning")
    (stop,) = evaluate(Facts(**base, ai_unknown_24h=5, ai_breaker_open=True))  # type: ignore[arg-type]
    assert (stop.rule, stop.severity, stop.key) == ("R4", "critical", "ai:breaker")


def test_the_budget_warns_at_eighty_percent_and_stops_at_a_hundred() -> None:
    base = {"loop_age": timedelta(0), "ai_active": True}
    assert rules(Facts(**base, ai_budget_used=Decimal("0.79"))) == []  # type: ignore[arg-type]
    (warn,) = evaluate(Facts(**base, ai_budget_used=Decimal("0.80")))  # type: ignore[arg-type]
    assert (warn.severity, warn.key) == ("warning", "ai:budget:80")
    (stop,) = evaluate(Facts(**base, ai_budget_used=Decimal("1.00")))  # type: ignore[arg-type]
    assert (stop.severity, stop.key) == ("critical", "ai:budget:100")


def test_an_unacknowledged_error_raises_r6() -> None:
    assert rules(Facts(loop_age=timedelta(0), unacknowledged_errors=2)) == ["R6"]


def test_every_rule_that_can_fire_is_documented_for_the_page() -> None:
    documented = {rule["id"] for rule in RULES}
    everything = Facts(
        loop_age=timedelta(hours=2),
        strategy_failures={"s": 9},
        price_age={"XBTEUR": None},
        ai_active=True,
        ai_unknown_24h=9,
        ai_breaker_open=True,
        ai_budget_used=Decimal(2),
        unacknowledged_errors=1,
    )
    assert set(rules(everything)) == documented
