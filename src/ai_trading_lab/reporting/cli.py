"""Explicit bounded notification preparation/delivery; never a background monitor."""

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.notifications.models import Notification, utc
from ai_trading_lab.notifications.store import NotificationStore
from ai_trading_lab.notifications.telegram import TelegramAdapter
from ai_trading_lab.reporting.service import ReportingService


def prepare(report: dict[str, Any], at: datetime, warning_fraction: Decimal) -> list[Notification]:
    at = utc(at)
    if not warning_fraction.is_finite() or not 0 < warning_fraction <= 1:
        raise ValueError("Explicit warning fraction must be between zero and one")
    identity = str(UUID(report["experiment_id"]))
    life = report["lifecycle"]
    sequence = life["last_sequence"]
    occurred = utc(datetime.fromisoformat(life["as_of_utc"]))
    if at < occurred:
        raise ValueError("Notification scan predates snapshot")
    summary_at = at.replace(hour=0, minute=0, second=0, microsecond=0)
    # Keys bind the durable snapshot. UTC daily summaries reuse the first recorded
    # snapshot that day; requeueing changed content under that key is refused.
    prefix = f"{identity}:{sequence}"
    result = [
        Notification(
            event_key=f"{prefix}:daily_summary:{at.date().isoformat()}",
            kind="daily_summary",
            occurred_at_utc=summary_at,
            text=(
                f"Experiment {identity}\nState {life['state']}; "
                f"cells {life['completed_cells']}/{life['total_cells']}\n"
                f"Economic net {report['consolidated_economic_net_pnl']} "
                f"{report['reporting_currency']} (None means unavailable)\n"
                "Synthetic/archived paper evidence; no profitability claim."
            ),
        )
    ]
    for cell in report["cells"]:
        cell_id = cell["cell_id"]
        base = f"{prefix}:{cell_id}"
        if cell.get("entry_suspended"):
            result.append(
                Notification(
                    event_key=f"{base}:halt",
                    kind="safety_halt",
                    occurred_at_utc=occurred,
                    text=(
                        f"Experiment {identity}; cell {cell_id}: entries suspended. "
                        "Deterministic controls remain authoritative."
                    ),
                )
            )
        if (cell.get("ai_metrics") or {}).get("failed_results", 0):
            result.append(
                Notification(
                    event_key=f"{base}:failure",
                    kind="failure",
                    occurred_at_utc=occurred,
                    text=(
                        f"Experiment {identity}; cell {cell_id}: recorded AI failure. "
                        "Inspect local audit."
                    ),
                )
            )
        budget = cell.get("inference_budget")
        if budget:
            with localcontext() as context:
                context.prec = 160
                spent = Decimal(budget["spent_estimated"]) + Decimal(budget["reserved"])
                limit = Decimal(budget["total_limit"])
                threshold_met = limit > 0 and spent >= limit * warning_fraction
            if budget["unresolved"] or threshold_met:
                result.append(
                    Notification(
                        event_key=f"{base}:budget",
                        kind="budget_warning",
                        occurred_at_utc=occurred,
                        text=(
                            f"Experiment {identity}; cell {cell_id}: estimated spend plus "
                            f"reservations {spent} {budget['currency']}; cap {limit}; "
                            f"unresolved {budget['unresolved']}."
                        ),
                    )
                )
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Explicit PAPER_ONLY reporting notifications")
    commands = root.add_subparsers(dest="command", required=True)
    queue = commands.add_parser("queue")
    queue.add_argument("experiment_id", type=UUID)
    queue.add_argument("--warning-fraction", type=Decimal, required=True)
    queue.add_argument("--at", type=datetime.fromisoformat, required=True)
    for name in ("show", "send", "recover-unknown"):
        sub = commands.add_parser(name)
        sub.add_argument("event_key")
        if name != "show":
            sub.add_argument("--at", type=datetime.fromisoformat, required=True)
    return root


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    store = NotificationStore(engine)
    try:
        if args.command == "queue":
            report = await ReportingService(engine).detail(args.experiment_id)
            output: object = [
                await store.enqueue(event, args.at)
                for event in prepare(report, args.at, args.warning_fraction)
            ]
        elif args.command == "show":
            output = await store.get(args.event_key)
        elif args.command == "recover-unknown":
            output = await store.recover_unknown(args.event_key, args.at)
        else:
            if (
                not settings.telegram_enabled
                or settings.telegram_bot_token is None
                or settings.telegram_chat_id is None
            ):
                raise ValueError("Telegram is disabled")
            adapter = TelegramAdapter(
                settings.telegram_bot_token, settings.telegram_chat_id, enabled=True
            )
            output = await store.dispatch(args.event_key, adapter, args.at)
    finally:
        await engine.dispose()
    print(json.dumps(output, sort_keys=True, default=str))
    return 0


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (ValueError, OSError, TimeoutError, LookupError, SQLAlchemyError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Invalid notification input, configuration or persistence",
                }
            )
        )
        code = 2
    raise SystemExit(code)
