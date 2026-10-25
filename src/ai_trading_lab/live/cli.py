"""``atl-live``: set up, run one cycle, run the loop, or look at the state. Paper only."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.engine import LiveEngine
from ai_trading_lab.live.errors import CycleErrors, LoopIntegrityError
from ai_trading_lab.live.status import summary
from ai_trading_lab.live.worker import (
    AlreadyRunning,
    run_loop,
    single_instance,
    stop_aware_sleep,
)
from ai_trading_lab.market_data.transport import HttpTransport

OFFSET_SECONDS = 20  # run just after a quarter hour so the newest 15-minute candle has closed


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="V0 live paper loop (PAPER_ONLY, no real orders)")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("setup", "create the strategies' paper portfolios (safe to repeat)"),
        ("cycle", "run a single live cycle against real Kraken data"),
        ("status", "print strategies, holdings, equity and recent decisions"),
    ):
        commands.add_parser(name, help=help_text)
    loop = commands.add_parser("run", help="run the loop")
    bound = loop.add_mutually_exclusive_group(required=True)
    bound.add_argument("--cycles", type=_positive, help="stop after this many cycles")
    bound.add_argument(
        "--until-stopped", action="store_true", help="run until SIGINT/SIGTERM (explicit opt-in)"
    )
    return root


def _emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, default=str), flush=True)


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    config = LiveConfig()
    live = LiveEngine(engine, lambda: HttpTransport(), config)
    try:
        if arguments.command == "setup":
            await live.setup()
            _emit(await summary(live))
        elif arguments.command == "status":
            _emit(await summary(live))
        elif arguments.command == "cycle":
            # A manual cycle takes the same lock as the loop, so two writers can never overlap.
            async with single_instance(engine):
                await live.setup()
                report = await live.run_cycle()
                _emit(report.model_dump(mode="json"))
            if report.errors:
                return 1
        else:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for name in ("SIGINT", "SIGTERM"):
                loop.add_signal_handler(getattr(signal, name), stop.set)
            async with single_instance(engine) as lease:
                await live.setup()

                async def cycle() -> Any:
                    report = await live.run_cycle()
                    if report.errors:
                        raise CycleErrors(report.errors)
                    return report.model_dump(mode="json")

                result = await run_loop(
                    cycle,
                    cycles=None if arguments.until_stopped else arguments.cycles,
                    interval=config.cycle_seconds,
                    offset=OFFSET_SECONDS,
                    clock=live.clock,
                    sleep=stop_aware_sleep(stop),
                    emit=_emit,
                    heartbeat=lease.verify,
                )
            return 0 if result.failed == 0 else 1
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except AlreadyRunning:
        print(json.dumps({"status": "failed", "error": "another live loop is already running"}))
        code = 3
    except LoopIntegrityError as exc:
        print(
            json.dumps({"status": "failed", "error": "integrity failure", "detail": str(exc)[:200]})
        )
        code = 4
    except (ValueError, OSError, TimeoutError, SQLAlchemyError, RuntimeError):
        print(
            json.dumps({"status": "failed", "error": "Invalid input, state or persistence failure"})
        )
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
