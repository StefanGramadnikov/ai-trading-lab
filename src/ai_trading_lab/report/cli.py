"""``atl-report``: build the evaluation report (read-only) and print it as Markdown or JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.report.render import markdown
from ai_trading_lab.report.service import ReportService


def _moment(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use a time like 2026-10-29T14:00:00+00:00") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("the time needs a zone, for example +00:00")
    return parsed.astimezone(UTC)


def _rate(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("a number such as 0.86 is required") from exc
    if not Decimal("0.01") <= rate <= Decimal(10):
        raise argparse.ArgumentTypeError("the rate must be between 0.01 and 10")
    return rate


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="atl-report", description=__doc__)
    root.add_argument("--since", type=_moment, help="ignore everything before this time")
    root.add_argument("--until", type=_moment, help="ignore everything after this time")
    root.add_argument(
        "--usd-eur",
        type=_rate,
        default=Decimal(1),
        help="EUR per USD for the AI bill (default 1, a conservative assumption)",
    )
    root.add_argument("--format", choices=("md", "json"), default="md")
    root.add_argument("--out", type=Path, help="write to this file instead of the screen")
    return root


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        report = await ReportService(engine, settings).build(
            since=arguments.since, until=arguments.until, usd_to_eur=arguments.usd_eur
        )
    finally:
        await engine.dispose()
    text = (
        markdown(report)
        if arguments.format == "md"
        else json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "written", "path": str(arguments.out)}))
    else:
        print(text, end="")
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
