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

from ai_trading_lab.alerts.monitor import MAX_FAILED_SENDS, AlertRunner
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


async def test_a_cleared_problem_sends_one_resolved_note_and_a_return_starts_a_new_episode(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    await watcher.tick(firing, NOW)
    quiet = overview()
    assert await watcher.tick(quiet, NOW + timedelta(minutes=5)) == {"sent": 1, "failed": 0}
    assert telegram.sent[-1].startswith("AI Trading Lab | PAPER ONLY | recovery\nResolved:")
    assert await watcher.tick(quiet, NOW + timedelta(minutes=10)) == {"sent": 0, "failed": 0}
    assert await watcher.tick(firing, NOW + timedelta(minutes=15)) == {"sent": 1, "failed": 0}
    assert len(telegram.sent) == 3
    assert (await AlertState(engine).rows())["loop"].episode == 2


async def test_a_telegram_outage_is_retried_a_few_times_then_waits_out_the_window(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    telegram.down = True
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    for attempt in range(MAX_FAILED_SENDS):
        counts = await watcher.tick(firing, NOW + timedelta(minutes=5 * attempt))
        assert counts == {"sent": 0, "failed": 1}
    after = NOW + timedelta(minutes=5 * MAX_FAILED_SENDS)
    assert await watcher.tick(firing, after) == {"sent": 0, "failed": 0}  # gave up for now
    telegram.down = False
    assert await watcher.tick(firing, after + timedelta(hours=6)) == {"sent": 1, "failed": 0}


async def test_recovery_of_telegram_delivers_a_pending_alert_on_the_next_check(
    engine: AsyncEngine,
) -> None:
    telegram = Telegram()
    watcher = runner(engine, telegram)
    firing = overview(("loop", "R1", ""))
    telegram.down = True
    assert await watcher.tick(firing, NOW) == {"sent": 0, "failed": 1}
    telegram.down = False
    assert await watcher.tick(firing, NOW + timedelta(minutes=5)) == {"sent": 1, "failed": 0}
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
