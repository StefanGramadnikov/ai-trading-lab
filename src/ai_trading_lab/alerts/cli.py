"""``atl-alerts``: run the alert watcher, send a test message, or find your Telegram chat id.

The bot token is read from the environment only (ATL_TELEGRAM_BOT_TOKEN) and is never printed,
logged or put in an error message: Telegram's URL contains it, so no exception text is shown.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.alerts.monitor import AlertRunner, DatabaseWatch, log
from ai_trading_lab.alerts.state import AlertState
from ai_trading_lab.config import Settings
from ai_trading_lab.dashboard.queries import DashboardData
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.store import NotificationStore
from ai_trading_lab.notifications.telegram import TelegramAdapter

INTERVAL_SECONDS = 300
HEALTH_FILE = Path("/tmp/alerts.ok")  # noqa: S108 - the container's own tmpfs


def adapter_from(settings: Settings) -> TelegramAdapter | None:
    if (
        not settings.telegram_enabled
        or settings.telegram_bot_token is None
        or settings.telegram_chat_id is None
    ):
        return None
    return TelegramAdapter(settings.telegram_bot_token, settings.telegram_chat_id, enabled=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="V0 Telegram alerts (PAPER_ONLY)")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="check every 5 minutes and send what needs sending")
    run.add_argument("--once", action="store_true", help="check once and exit")
    commands.add_parser("test", help="send one test message to your Telegram chat")
    commands.add_parser(
        "find-chat", help="after you message your bot, print the chat id to put in the env file"
    )
    return root


async def find_chat(settings: Settings, transport: httpx2.AsyncBaseTransport | None = None) -> int:
    token = settings.telegram_bot_token
    if token is None:
        print(json.dumps({"status": "failed", "error": "ATL_TELEGRAM_BOT_TOKEN is not set"}))
        return 2
    try:
        async with httpx2.AsyncClient(
            transport=transport, timeout=10, trust_env=False, follow_redirects=False
        ) as client:
            reply = await client.get(
                f"https://api.telegram.org/bot{token.get_secret_value()}/getUpdates"
            )
        data: Any = reply.json()
    except (httpx2.HTTPError, ValueError):
        print(json.dumps({"status": "failed", "error": "Telegram did not answer"}))
        return 2
    if reply.status_code != 200 or not isinstance(data, dict) or data.get("ok") is not True:
        print(json.dumps({"status": "failed", "error": "Telegram refused the token"}))
        return 2
    chats: dict[int, dict[str, str]] = {}
    for update in data.get("result", []):
        chat = (update.get("message") or {}).get("chat") or {}
        if isinstance(chat.get("id"), int):
            chats[chat["id"]] = {
                "id": str(chat["id"]),
                "type": str(chat.get("type", "")),
                "name": str(chat.get("first_name") or chat.get("title") or ""),
            }
    if not chats:
        print(
            json.dumps(
                {"status": "no_chat_yet", "hint": "Send any message to your bot, then retry"}
            )
        )
        return 1
    print(json.dumps({"chats": list(chats.values())}))
    return 0


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if arguments.command == "find-chat":
        return await find_chat(settings)
    engine = create_database_engine(settings)
    try:
        adapter = adapter_from(settings)
    except ValueError:
        # A mistyped token or chat id: say so (never the value) and idle instead of crash-looping.
        log({"alerts": "telegram_settings_invalid", "hint": "check the token and chat id"})
        adapter = None
    outbox = NotificationStore(engine)
    try:
        if arguments.command == "test":
            if adapter is None:
                print(json.dumps({"status": "failed", "error": "Telegram is not configured"}))
                return 2
            now = datetime.now(UTC)
            event = Notification(
                event_key=f"test:{now.strftime('%Y%m%dT%H%M%S')}",
                kind="daily_summary",
                occurred_at_utc=now,
                text="Test message: alerts are connected. Nothing is wrong.",
            )
            await outbox.enqueue(event, now)
            result = await outbox.dispatch(event.event_key, adapter, now)
            print(json.dumps({"state": result["state"], "reason": result["reason"]}))
            return 0 if result["state"] == "delivered" else 1
        data = DashboardData(engine, settings)
        journal = ErrorJournal(engine)
        runner = AlertRunner(AlertState(engine), outbox, adapter)
        watch = DatabaseWatch()
        if not runner.sending:
            log({"alerts": "telegram_not_configured", "sending": False})
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            loop.add_signal_handler(getattr(signal, name), stop.set)
        while not stop.is_set():
            now = datetime.now(UTC)
            try:
                if runner.sending:
                    counts = await runner.tick(await data.overview(), now)
                    if counts["sent"] or counts["failed"]:
                        log({"alerts": counts, "at": now.isoformat()})
                    await watch.up(adapter, now)
                await asyncio.to_thread(HEALTH_FILE.touch)
            except Exception as exc:  # noqa: BLE001 - nothing may kill the watcher
                log({"alerts": "check_failed", "error": type(exc).__name__})
                if isinstance(exc, SQLAlchemyError | OSError | TimeoutError):
                    # The outbox lives in the database, so say so without it.
                    try:
                        await watch.down(adapter, now)
                    except Exception as sending:  # noqa: BLE001
                        log({"alerts": "db_notice_failed", "error": type(sending).__name__})
                else:
                    try:
                        await journal.record_exception("alerts", exc, at=now)
                    except Exception:  # noqa: BLE001
                        pass
                # Alive and trying: the health file is for "is the watcher running".
                await asyncio.to_thread(HEALTH_FILE.touch)
            if arguments.once:
                break
            try:
                await asyncio.wait_for(stop.wait(), INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (ValueError, OSError, SQLAlchemyError):
        print(json.dumps({"status": "failed", "error": "Invalid configuration or persistence"}))
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
