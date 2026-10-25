"""Explicit local runtime commands; creating a configuration never starts a worker."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.market_data.models import parse_json
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.runtime.notifications import queue_checkpoint
from ai_trading_lab.runtime.worker import run_worker


def read_json(path: Path) -> object:
    with path.open("rb") as handle:
        raw = handle.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Runtime input exceeds 2MB")
    return parse_json(raw.decode("utf-8"))


MAX_CONTROL_SKEW = timedelta(seconds=30)


def control_at(stamp: datetime | None, *, now: datetime | None = None) -> datetime:
    """Operator controls are stamped by the wall clock; a typo cannot move the journal clock."""
    current = now if now is not None else datetime.now(UTC)
    if stamp is None:
        return current
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("runtime_control_clock_requires_timezone")
    if abs(stamp - current) > MAX_CONTROL_SKEW:
        raise ValueError("runtime_control_clock_skew")
    return stamp.astimezone(UTC)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Bounded local PAPER_ONLY runtime")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("configuration", type=Path)
    for name in ("show", "step", "pause", "resume", "stop", "worker"):
        command = commands.add_parser(name)
        command.add_argument("session_id", type=UUID)
        if name == "worker":
            command.add_argument("--cycles", type=int, required=True)
            command.add_argument("--seconds", type=int, required=True)
            command.add_argument("--activate-observed", action="store_true", required=True)
        elif name != "show":
            command.add_argument("--key", required=True)
            # Controls default to the current wall clock; only fixture steps are time-travel.
            command.add_argument("--at", type=datetime.fromisoformat, required=name == "step")
            if name == "step":
                command.add_argument("event", type=Path)
    return root


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    journal = Journal(engine, reducer)
    try:
        if args.command == "create":
            config = RuntimeConfig.model_validate(
                await asyncio.to_thread(read_json, args.configuration)
            )
            result = await journal.create(
                config.session_id, config.model_dump(mode="json"), config.started_at_utc
            )
        elif args.command == "show":
            result = await journal.get(args.session_id)
        elif args.command == "worker":
            result = await run_worker(
                journal, args.session_id, cycles=args.cycles, seconds=args.seconds
            )
        else:
            if args.command == "step":
                step_config = await journal.config(args.session_id)
                if step_config.get("data_mode") == "OBSERVED":
                    raise ValueError("OBSERVED sessions require the lease-holding worker")
                raw = await asyncio.to_thread(read_json, args.event)
                if not isinstance(raw, dict) or raw.get("action") != "tick":
                    raise ValueError("Explicit tick event required")
                payload: dict[str, object] = {str(k): v for k, v in raw.items()}
                at = args.at
            else:
                payload = {"action": args.command}
                session = await journal.config(args.session_id)
                if session.get("data_mode") == "OBSERVED":
                    at = control_at(args.at)
                elif args.at is None:
                    raise ValueError("Fixture sessions require an explicit --at")
                else:
                    at = args.at
            result = await journal.append(args.session_id, args.key, payload, at)
            await queue_checkpoint(engine, result, at)
    finally:
        await engine.dispose()
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (ValueError, LookupError, OSError, TimeoutError, SQLAlchemyError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Runtime input, ownership, code or persistence conflict",
                }
            )
        )
        code = 2
    raise SystemExit(code)
