"""``atl-review``: build or read a daily review and answer its suggestions.

Answering a suggestion only records the answer. Nothing here changes a strategy, a risk limit or a
configuration: a change is made by a person as a new, versioned experiment configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.live.cli import _user
from ai_trading_lab.review.service import REVIEW_AFTER, ReviewService
from ai_trading_lab.review.store import ReviewStore, Suggestion


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use a date like 2026-10-30") from exc


def _reason(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 300:
        raise argparse.ArgumentTypeError("a short reason (1-300 characters) is required")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="atl-review", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="build and store a day's review (default: yesterday)")
    run.add_argument("--day", type=_day)
    show = commands.add_parser("show", help="print a stored review (default: the newest)")
    show.add_argument("--day", type=_day)
    listing = commands.add_parser("suggestions", help="list suggestions")
    listing.add_argument("--status", choices=("pending", "approved", "rejected"))
    for name in ("approve", "reject"):
        answer = commands.add_parser(name, help=f"{name} a suggestion (records the answer only)")
        answer.add_argument("suggestion_id")
        answer.add_argument("--reason", required=True, type=_reason)
    return root


def _out(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, default=str), flush=True)


def _row(s: Suggestion) -> dict[str, Any]:
    return {
        "id": s.suggestion_id,
        "status": s.status,
        "strategy": s.strategy_id or None,
        "what_we_saw": s.text,
        "suggestion": s.suggestion,
        "decided_by": s.decided_by,
        "reason": s.decision_reason,
    }


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        store = ReviewStore(engine)
        now = datetime.now(UTC)
        if arguments.command == "run":
            newest = (now - REVIEW_AFTER).date() - timedelta(days=1)
            day = arguments.day or newest
            if day > newest:  # a review is never rewritten: a partial day would be frozen
                _out({"status": "not_over_yet", "day": day.isoformat()})
                return 1
            service = ReviewService(engine, settings)
            if await store.has(day):
                _out({"status": "exists", "day": day.isoformat()})
                return 0
            review = await service.create(day, now)
            if review is None:
                _out({"status": "no_data", "day": day.isoformat()})
                return 1
            stored = await store.get(day)
            _out(
                {"status": "created", "day": day.isoformat(), "text": stored.text if stored else ""}
            )
            return 0
        if arguments.command == "show":
            if arguments.day is not None:
                found = await store.get(arguments.day)
                shown = [found] if found else []
            else:
                shown = await store.latest(1)
            if not shown:
                _out({"status": "none"})
                return 1
            print(shown[0].text)
            return 0
        if arguments.command == "suggestions":
            _out({"suggestions": [_row(s) for s in await store.suggestions(arguments.status)]})
            return 0
        status = "approved" if arguments.command == "approve" else "rejected"
        done = await store.decide(
            arguments.suggestion_id, status, f"cli:{_user()}", arguments.reason, now
        )
        _out({"status": status if done else "not_pending", "id": arguments.suggestion_id})
        return 0 if done else 1
    finally:
        await engine.dispose()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (ValueError, OSError, SQLAlchemyError):
        print(json.dumps({"status": "failed", "error": "Invalid configuration or persistence"}))
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
