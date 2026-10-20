"""Explicit bounded paper experiment commands; no provider activation or deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.experiments.models import ExperimentConfig
from ai_trading_lab.experiments.store import ExperimentStore
from ai_trading_lab.market_data.models import parse_json


def read_config(path: Path) -> ExperimentConfig:
    with path.open("rb") as handle:
        data = handle.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("experiment input exceeds 2MB")
    return ExperimentConfig.model_validate(parse_json(data.decode("utf-8")))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Immutable PAPER_ONLY experiment evaluation")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("scenario", type=Path)
    for name in ("create", "start", "pause", "resume", "approve", "reject", "run", "report"):
        command = create if name == "create" else commands.add_parser(name)
        if name != "create":
            command.add_argument("experiment_id", type=UUID)
        if name != "report":
            command.add_argument("--key", required=True)
            command.add_argument("--at", type=datetime.fromisoformat, required=True)
        if name not in ("create", "run", "report"):
            command.add_argument("--reason", required=True)
    return result


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    config = (
        await asyncio.to_thread(read_config, arguments.scenario)
        if arguments.command == "create"
        else None
    )
    engine = create_database_engine(settings)
    store = ExperimentStore(engine)
    try:
        if config is not None:
            output = await store.create(config, arguments.key, arguments.at)
        elif arguments.command == "report":
            output = await store.report(arguments.experiment_id)
        elif arguments.command == "run":
            output = await store.advance(arguments.experiment_id, arguments.key, arguments.at)
        else:
            output = await store.command(
                arguments.experiment_id,
                arguments.key,
                arguments.command,
                arguments.at,
                arguments.reason,
            )
    finally:
        await engine.dispose()
    print(json.dumps(output, sort_keys=True))
    return 0


def main() -> None:
    arguments = parser().parse_args()
    try:
        code = asyncio.run(run(arguments))
    except (ValueError, OSError, TimeoutError, SQLAlchemyError):
        print(
            json.dumps(
                {"status": "failed", "error": "Invalid experiment input, state or persistence"}
            )
        )
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
