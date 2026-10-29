"""Which alerts go out: once, a 6 hour reminder, one 'resolved' note, bounded retries."""

from datetime import UTC, datetime, timedelta

from ai_trading_lab.alerts.monitor import (
    MAX_FAILED_SENDS,
    Action,
    heartbeat,
    notification,
    plan,
)
from ai_trading_lab.alerts.state import Row
from ai_trading_lab.dashboard.alerts import Finding

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def finding(key: str = "prices:XBTEUR", rule: str = "R3", severity: str = "critical") -> Finding:
    return Finding(rule, severity, "Kraken prices are not arriving", "XBTEUR is 50 min old.", key)  # type: ignore[arg-type]


def row(
    *,
    sent: datetime | None,
    resolved: datetime | None = None,
    failed: int = 0,
    episode: int = 1,
    key: str = "prices:XBTEUR",
) -> Row:
    return Row(
        key=key,
        rule="R3",
        severity="critical",
        title="Kraken prices are not arriving",
        detail="old",
        first_fired_utc=NOW - timedelta(hours=9),
        last_sent_utc=sent,
        resolved_at_utc=resolved,
        episode=episode,
        failed_sends=failed,
    )


def verbs(actions: list[Action]) -> list[str]:
    return [a.verb for a in actions]


def test_a_new_finding_is_sent_once_as_episode_one() -> None:
    (action,) = plan(NOW, [finding()], {})
    assert (action.verb, action.episode, action.first_fired) == ("fire", 1, NOW)


def test_a_finding_already_told_is_silent_until_six_hours_have_passed() -> None:
    told = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=5, minutes=59))}
    assert plan(NOW, [finding()], told) == []
    told = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=6))}
    assert verbs(plan(NOW, [finding()], told)) == ["repeat"]


def test_a_finding_that_came_back_after_resolving_starts_a_new_episode() -> None:
    done = {
        "prices:XBTEUR": row(sent=NOW - timedelta(hours=1), resolved=NOW - timedelta(minutes=5))
    }
    (action,) = plan(NOW, [finding()], done)
    assert (action.verb, action.episode) == ("fire", 2)


def test_a_failed_delivery_is_retried_on_the_next_check_but_only_a_few_times() -> None:
    assert verbs(plan(NOW, [finding()], {"prices:XBTEUR": row(sent=None, failed=2)})) == ["repeat"]
    given_up = {"prices:XBTEUR": row(sent=None, failed=MAX_FAILED_SENDS)}
    assert plan(NOW, [finding()], given_up) == []


def test_a_problem_that_cleared_is_announced_once_when_the_owner_had_been_told() -> None:
    told = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=1))}
    (action,) = plan(NOW, [], told)
    assert action.verb == "resolve"
    cleared = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=1), resolved=NOW)}
    assert plan(NOW, [], cleared) == []


def test_a_problem_the_owner_never_heard_about_resolves_silently() -> None:
    assert plan(NOW, [], {"prices:XBTEUR": row(sent=None)}) == []


def test_each_finding_is_handled_on_its_own_key() -> None:
    both = [finding("prices:XBTEUR"), finding("prices:ETHEUR")]
    told = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=1))}
    (action,) = plan(NOW, both, told)
    assert (action.verb, action.key) == ("fire", "prices:ETHEUR")


def test_the_message_names_the_rule_the_severity_and_how_to_open_the_dashboard() -> None:
    (action,) = plan(NOW, [finding()], {})
    message = notification(action, NOW)
    assert message.kind == "failure"
    assert "CRITICAL" in message.text and "R3" in message.text and "localhost:8000" in message.text
    assert message.event_key == "alert:prices:XBTEUR:1:fire:20261029T120000Z"
    assert "PAPER ONLY" in message.message()


def test_a_reminder_says_so_and_the_budget_rule_uses_the_budget_kind() -> None:
    told = {"ai:budget:80": row(sent=NOW - timedelta(hours=7), key="ai:budget:80")}
    (action,) = plan(NOW, [finding("ai:budget:80", "R5", "warning")], told)
    message = notification(action, NOW)
    assert (action.verb, message.kind) == ("repeat", "budget_warning")
    assert "Reminder" in message.text and "Warning:" in message.text


def test_the_resolved_message_is_a_recovery_notice() -> None:
    told = {"prices:XBTEUR": row(sent=NOW - timedelta(hours=1))}
    (action,) = plan(NOW, [], told)
    message = notification(action, NOW)
    assert message.kind == "recovery" and message.text.startswith("Resolved:")


def test_the_daily_check_summarises_values_alerts_errors_and_the_ai() -> None:
    overview = {
        "strategies": [
            {"label": "Rules breakout", "equity": "101.20"},
            {"label": "AI", "equity": None},
        ],
        "alerts": {"firing": [{"rule": "R3"}]},
        "errors": {"unacknowledged_errors": 2},
        "ai": {"active": False},
    }
    text = heartbeat(overview, NOW).text
    assert "Rules breakout 101.20 EUR" in text and "AI no data" in text
    assert "Alerts active now: 1" in text and "Errors to review: 2" in text and "AI: off" in text
    assert heartbeat(overview, NOW).event_key == "heartbeat:20261029"
