"""Bounded offline research CLI; no external market or execution requests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.backtesting.store import ResearchStore
from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.market_data.models import parse_json


def read_research(path: Path) -> ResearchInput:
    with path.open("rb") as handle:
        content = handle.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError("Research input exceeds 2MB input bound")
    return ResearchInput.model_validate(parse_json(content.decode("utf-8")))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline research backtest; PAPER_ONLY")
    commands = result.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Evaluate a bounded immutable research tape")
    command.add_argument("research", type=Path)
    return result


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    spec = await asyncio.to_thread(read_research, Path(arguments.research))
    engine = create_database_engine(settings)
    try:
        result = await ResearchStore(engine).run(spec)
    finally:
        await engine.dispose()
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> None:
    arguments = parser().parse_args()
    try:
        result = asyncio.run(run(arguments))
    except (SQLAlchemyError, OSError, ValueError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Invalid research input, configuration or persistence failure",
                }
            )
        )
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
