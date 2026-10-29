"""The alert runner on PostgreSQL with a mocked Telegram: delivery, repeats, retries, resolution."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.alerts.monitor import (
    BACKOFF,
    QUIET_BEFORE_RESOLVE,
    AlertRunner,
    DatabaseWatch,
)
from ai_trading_lab.alerts.state import AlertState, alert_state
from ai_trading_lab.notifications.store import NotificationStore, notifications
from ai_trading_lab.notifications.telegram import TelegramAdapter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
TOKEN = SecretStr("123456:fixture_only_abcdefghijklmnopqrst")
CHAT = SecretStr("-123456")


class Telegram:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.down = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            return httpx2.Response(500, json={"ok": False})
        self.sent.append(json.loads(request.content)["text"])
        return httpx2.Response(
            200,
            json={"ok": True, "result": {"message_id": len(self.sent), "chat": {"id": -123456}}},
        )


def overview(*findings: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "alerts": {
            "firing": [
                {
                    "rule": rule,
                    "severity": "critical",
                    "title": f"Title {rule}",
                    "detail": f"Detail {rule}",
                    "key": key,
                    "strategy_id": None,
                }
                for key, rule, _ in findings
            ]
        },
        "strategies": [{"label": "Rules breakout", "equity": "100.50"}],
        "errors": {"unacknowledged_errors": 0},
        "ai": {"active": False},
    }


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    for table in (alert_state, notifications):
        async with value.begin() as connection:
            await connection.execute(delete(table))
    try:
        yield value
    finally:
        for table in (alert_state, notifications):
            async with value.begin() as connection:
                await connection.execute(delete(table))
        await value.dispose()


def runner(engine: AsyncEngine, telegram: Telegram, *, heartbeat: int | None = None) -> AlertRunner:
    adapter = TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(telegram.handle)
    )
    return AlertRunner(
        AlertState(engine), NotificationStore(engine), adapter, heartbeat_hour_utc=heartbeat
    )


async def test_a_firing_alert_is_sent_once_then_reminded_after_six_hours(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("prices:XBTEUR", "R3", ""))
    assert await watcher.tick(firing, NOW) == {"sent": 1, "failed": 0}
    assert "CRITICAL: Title R3" in telegram.sent[0]
    for minutes in (5, 60, 300, 359):  # five minute checks never repeat it inside 6 hours
        assert await watcher.tick(firing, NOW + timedelta(minutes=minutes)) == {
            "sent": 0,
            "failed": 0,
        }
    assert await watcher.tick(firing, NOW + timedelta(hours=6)) == {"sent": 1, "failed": 0}
    assert "Reminder" in telegram.sent[1] and len(telegram.sent) == 2


async def test_a_restart_does_not_repeat_what_the_owner_already_heard(engine: AsyncEngine) -> None:
    telegram = Telegram()
    firing = overview(("loop", "R1", ""))
    await runner(engine, telegram).tick(firing, NOW)
    fresh = runner(engine, telegram)  # a new process reading the same state
    assert await fresh.tick(firing, NOW + timedelta(minutes=5)) == {"sent": 0, "failed": 0}
    assert len(telegram.sent) == 1


async def test_a_cleared_problem_sends_one_resolved_note_after_half_an_hour_of_quiet(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    quiet = overview()
    await watcher.tick(firing, NOW)
    # A blip: quiet for ten minutes, then firing again, is still the same episode and silent.
    assert await watcher.tick(quiet, NOW + timedelta(minutes=10)) == {"sent": 0, "failed": 0}
    assert await watcher.tick(firing, NOW + timedelta(minutes=15)) == {"sent": 0, "failed": 0}
    start = NOW + timedelta(minutes=15)
    assert await watcher.tick(quiet, start + timedelta(minutes=20)) == {"sent": 0, "failed": 0}
    done = start + QUIET_BEFORE_RESOLVE
    assert await watcher.tick(quiet, done) == {"sent": 1, "failed": 0}
    assert telegram.sent[-1].startswith("AI Trading Lab | PAPER ONLY | recovery\nResolved:")
    assert await watcher.tick(quiet, done + timedelta(minutes=5)) == {"sent": 0, "failed": 0}
    assert await watcher.tick(firing, done + timedelta(minutes=10)) == {"sent": 1, "failed": 0}
    assert len(telegram.sent) == 3
    assert (await AlertState(engine).rows())["loop"].episode == 2


async def test_a_telegram_outage_is_retried_with_growing_pauses(engine: AsyncEngine) -> None:
    telegram = Telegram()
    telegram.down = True
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    when = NOW
    assert await watcher.tick(firing, when) == {"sent": 0, "failed": 1}
    for wait in BACKOFF[:3]:
        assert await watcher.tick(firing, when + wait / 2) == {"sent": 0, "failed": 0}  # too soon
        when += wait
        assert await watcher.tick(firing, when) == {"sent": 0, "failed": 1}
    row = (await AlertState(engine).rows())["loop"]
    assert (row.failed_sends, row.last_sent_utc) == (4, None)  # never marked as told
    telegram.down = False
    when += BACKOFF[3]
    assert await watcher.tick(firing, when) == {"sent": 1, "failed": 0}
    assert "Reminder" not in telegram.sent[0]  # the owner's first message, not a reminder


async def test_a_returning_alert_whose_first_message_fails_does_not_look_already_told(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing, quiet = overview(("loop", "R1", "")), overview()
    await watcher.tick(firing, NOW)
    await watcher.tick(quiet, NOW + timedelta(minutes=5))
    await watcher.tick(quiet, NOW + QUIET_BEFORE_RESOLVE + timedelta(minutes=5))  # resolved
    telegram.down = True
    back = NOW + timedelta(hours=1)
    assert await watcher.tick(firing, back) == {"sent": 0, "failed": 1}
    row = (await AlertState(engine).rows())["loop"]
    assert row.last_sent_utc is None and row.episode == 2


async def test_a_failure_after_delivery_is_retried_but_an_alert_marked_as_told_is_not_resent(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    real = watcher.outbox.dispatch

    async def accepted_then_boom(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)  # Telegram got it
        raise OSError("connection lost after delivery")

    monkeypatch.setattr(watcher.outbox, "dispatch", accepted_then_boom)
    await watcher.tick(firing, NOW)
    monkeypatch.undo()
    # The failure was treated as undelivered and rolled back: documented unknown-outcome trade-off.
    row = (await AlertState(engine).rows())["loop"]
    assert row.last_sent_utc is None and row.failed_sends == 1
    # But a crash of the whole process between the save and the send leaves it marked as told:
    told = row.__class__(**{**row.__dict__, "last_sent_utc": NOW, "failed_sends": 0})
    await AlertState(engine).save(told)
    assert await watcher.tick(firing, NOW + timedelta(minutes=5)) == {"sent": 0, "failed": 0}


async def test_one_failing_action_does_not_stop_the_others_or_the_daily_check(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram, heartbeat=7)
    real = watcher.outbox.enqueue
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("database slow")
        return await real(*args, **kwargs)

    monkeypatch.setattr(watcher.outbox, "enqueue", flaky)
    two = overview(("loop", "R1", ""), ("prices:XBTEUR", "R3", ""))
    counts = await watcher.tick(two, NOW)
    assert counts == {"sent": 2, "failed": 1}  # first alert failed, second alert and heartbeat sent


async def test_a_problem_never_delivered_is_closed_quietly_when_it_goes_away(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    telegram.down = True
    watcher = runner(engine, telegram)
    await watcher.tick(overview(("loop", "R1", "")), NOW)
    telegram.down = False
    assert await watcher.tick(overview(), NOW + timedelta(hours=1)) == {"sent": 0, "failed": 0}
    assert (await AlertState(engine).rows())["loop"].resolved_at_utc is not None
    assert telegram.sent == []


async def test_recovery_of_telegram_delivers_a_pending_alert_on_the_next_check(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    telegram.down = True
    assert await watcher.tick(firing, NOW) == {"sent": 0, "failed": 1}
    telegram.down = False
    assert await watcher.tick(firing, NOW + BACKOFF[0]) == {"sent": 1, "failed": 0}
    assert len(telegram.sent) == 1


async def test_nothing_is_marked_as_told_while_telegram_is_not_configured(
    engine: AsyncEngine,
) -> None:
    watcher = AlertRunner(AlertState(engine), NotificationStore(engine), None)
    assert watcher.sending is False
    assert await watcher.tick(overview(("loop", "R1", "")), NOW) == {"sent": 0, "failed": 0}
    assert await AlertState(engine).rows() == {}


async def test_the_daily_check_goes_out_once_a_day_after_its_hour(engine: AsyncEngine) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram, heartbeat=7)
    quiet = overview()
    early = NOW.replace(hour=6)
    assert await watcher.tick(quiet, early) == {"sent": 0, "failed": 0}
    assert await watcher.tick(quiet, NOW.replace(hour=7)) == {"sent": 1, "failed": 0}
    assert await watcher.tick(quiet, NOW.replace(hour=9)) == {"sent": 0, "failed": 0}
    assert await watcher.tick(quiet, NOW + timedelta(days=1, hours=-5)) == {"sent": 1, "failed": 0}
    assert "Daily check" in telegram.sent[0]


async def test_a_daily_check_that_failed_is_retried_later_the_same_day(engine: AsyncEngine) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram, heartbeat=7)
    seven = NOW.replace(hour=7)
    telegram.down = True
    assert await watcher.tick(overview(), seven) == {"sent": 0, "failed": 1}
    assert await watcher.tick(overview(), seven + timedelta(minutes=1)) == {"sent": 0, "failed": 0}
    telegram.down = False
    assert await watcher.tick(overview(), seven + BACKOFF[0]) == {"sent": 1, "failed": 0}
    assert await watcher.tick(overview(), seven + timedelta(hours=3)) == {"sent": 0, "failed": 0}


async def test_a_secret_shaped_string_never_reaches_telegram(engine: AsyncEngine) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    leaky = overview(("loop", "R1", ""))
    leaky["alerts"]["firing"][0]["detail"] = "key sk-abcdefghijklmnopqrstuvwx leaked"
    other = overview(("prices:XBTEUR", "R3", ""))
    leaky["alerts"]["firing"].append(other["alerts"]["firing"][0])
    assert await watcher.tick(leaky, NOW) == {"sent": 1, "failed": 1}  # the refused one only
    assert len(telegram.sent) == 1 and "sk-" not in telegram.sent[0]


async def test_one_pass_of_the_watcher_touches_its_health_file_and_sends_nothing_unconfigured(
    engine: AsyncEngine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import argparse

    from ai_trading_lab.alerts import cli

    marker = tmp_path / "alerts.ok"
    monkeypatch.setattr(cli, "HEALTH_FILE", marker)
    monkeypatch.setenv("ATL_DATABASE_URL", database_url)
    code = await cli.run(argparse.Namespace(command="run", once=True))
    assert code == 0 and marker.exists()
    assert await AlertState(engine).rows() == {}


async def test_the_test_command_refuses_without_telegram_and_delivers_with_it(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, database_url: str, capsys: Any
) -> None:
    import argparse

    from ai_trading_lab.alerts import cli

    monkeypatch.setenv("ATL_DATABASE_URL", database_url)
    assert await cli.run(argparse.Namespace(command="test")) == 2
    assert "not configured" in capsys.readouterr().out
    telegram = Telegram()
    adapter = TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(telegram.handle)
    )
    monkeypatch.setattr(cli, "adapter_from", lambda settings: adapter)
    assert await cli.run(argparse.Namespace(command="test")) == 0
    assert "alerts are connected" in telegram.sent[0]


async def test_a_database_outage_is_announced_once_per_six_hours_and_then_resolved() -> None:
    telegram = Telegram()
    adapter = TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(telegram.handle)
    )
    watch = DatabaseWatch()
    assert await watch.down(adapter, NOW) is True
    assert "database does not answer" in telegram.sent[0]
    assert await watch.down(adapter, NOW + timedelta(minutes=5)) is False
    assert await watch.down(adapter, NOW + timedelta(hours=6)) is True
    await watch.up(adapter, NOW + timedelta(hours=7))
    assert telegram.sent[-1].endswith("Resolved: the database answers again.")
    await watch.up(adapter, NOW + timedelta(hours=8))  # nothing more once it was announced
    assert len(telegram.sent) == 3
    assert await DatabaseWatch().down(None, NOW) is False


async def test_a_telegram_failure_while_announcing_a_database_outage_is_retried_next_check() -> (
    None
):
    telegram = Telegram()
    telegram.down = True
    adapter = TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(telegram.handle)
    )
    watch = DatabaseWatch()
    assert await watch.down(adapter, NOW) is False
    telegram.down = False
    assert await watch.down(adapter, NOW + timedelta(minutes=5)) is True


async def test_the_watcher_survives_any_error_in_a_check_and_still_reports_alive(
    engine: AsyncEngine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import argparse

    from ai_trading_lab.alerts import cli
    from ai_trading_lab.dashboard.queries import DashboardData

    telegram = Telegram()
    adapter = TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(telegram.handle)
    )
    monkeypatch.setattr(cli, "adapter_from", lambda settings: adapter)
    marker = tmp_path / "alerts.ok"
    monkeypatch.setattr(cli, "HEALTH_FILE", marker)
    monkeypatch.setenv("ATL_DATABASE_URL", database_url)

    async def odd(self: Any) -> Any:
        raise KeyError("unexpected shape")

    monkeypatch.setattr(DashboardData, "overview", odd)
    assert await cli.run(argparse.Namespace(command="run", once=True)) == 0
    assert marker.exists()
    assert telegram.sent == []  # a programming error is journaled, not announced as a db outage

    async def down(self: Any) -> Any:
        from sqlalchemy.exc import OperationalError

        raise OperationalError("select 1", {}, Exception("refused"))

    monkeypatch.setattr(DashboardData, "overview", down)
    assert await cli.run(argparse.Namespace(command="run", once=True)) == 0
    assert "database does not answer" in telegram.sent[0]


async def test_a_mistyped_token_is_reported_without_the_value_and_does_not_crash(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, database_url: str, capsys: Any
) -> None:
    import argparse

    from ai_trading_lab.alerts import cli

    monkeypatch.setattr(cli, "HEALTH_FILE", tmp_path / "alerts.ok")
    monkeypatch.setenv("ATL_DATABASE_URL", database_url)
    monkeypatch.setenv("ATL_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ATL_TELEGRAM_BOT_TOKEN", "not-a-real-token-shape")
    monkeypatch.setenv("ATL_TELEGRAM_CHAT_ID", "42")
    assert await cli.run(argparse.Namespace(command="run", once=True)) == 0
    out = capsys.readouterr().out
    assert "telegram_settings_invalid" in out and "not-a-real-token-shape" not in out
