"""``atl-live``: set up, run one cycle, run the loop, or look at the state. Paper only."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import signal
import sys
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.providers import OPENAI_REASONING_EFFORT, HTTPProvider
from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.live.ai_strategy import AIConfig, AIDeps
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.engine import LiveEngine
from ai_trading_lab.live.errors import CycleErrors, LoopIntegrityError
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.live.status import health, summary
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


def _user() -> str:
    """Informational only: the OS user, or its numeric id where there is no passwd entry."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return f"uid:{os.getuid()}"


def _fingerprint(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{4,64}", value):
        raise argparse.ArgumentTypeError("must be 4 to 64 hex characters (a fingerprint prefix)")
    return value


def _fingerprint_or_all(value: str) -> str:
    return value if value == "all" else _fingerprint(value)


def _reason(value: str) -> str:
    text = value.strip()
    if not 1 <= len(text) <= 200:
        raise argparse.ArgumentTypeError("must be 1 to 200 characters")
    return text


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="V0 live paper loop (PAPER_ONLY, no real orders)")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("setup", "create the strategies' paper portfolios (safe to repeat)"),
        ("cycle", "run a single live cycle against real Kraken data"),
        ("status", "print strategies, holdings, equity and recent decisions"),
    ):
        commands.add_parser(name, help=help_text)
    for name, help_text in (
        ("enable", "switch a strategy on (takes effect at the next cycle)"),
        ("disable", "switch a strategy off: no new entries or AI calls, exits still run"),
    ):
        toggle = commands.add_parser(name, help=help_text)
        toggle.add_argument("strategy", help="rules, ai, benchmark, or a full strategy id")
        toggle.add_argument(
            "--reason", required=True, type=_reason, help="why (recorded in the audit trail)"
        )
    probe = commands.add_parser("health", help="exit 0 if the loop recorded equity recently")
    probe.add_argument("--max-age-minutes", type=_positive, default=40)
    errors = commands.add_parser("errors", help="list recorded problems, or show one in full")
    errors.add_argument(
        "--show", metavar="FINGERPRINT", type=_fingerprint, help="full detail for one problem"
    )
    errors.add_argument("--status", choices=("new", "seen", "acknowledged"))
    errors.add_argument("--level", choices=("error", "warning"))
    errors.add_argument("--limit", type=_positive, default=30)
    ack = commands.add_parser("ack", help="acknowledge a recorded error (or all new ones)")
    ack.add_argument("fingerprint", type=_fingerprint_or_all, help="a fingerprint prefix, or 'all'")
    ack.add_argument("--reason", required=True, type=_reason)
    audit = commands.add_parser("audit", help="print the on/off audit trail, newest first")
    audit.add_argument("--limit", type=_positive, default=20)
    loop = commands.add_parser("run", help="run the loop")
    bound = loop.add_mutually_exclusive_group(required=True)
    bound.add_argument("--cycles", type=_positive, help="stop after this many cycles")
    bound.add_argument(
        "--until-stopped", action="store_true", help="run until SIGINT/SIGTERM (explicit opt-in)"
    )
    return root


def _emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, default=str), flush=True)


def build_ai(settings: Settings, engine: AsyncEngine) -> tuple[AIDeps | None, str]:
    """The AI strategy exists only when explicitly activated and fully configured.

    Needs ATL_AI_ENABLED, an OpenAI key, a spending cap above zero and a reviewed price for the
    model. Anything missing leaves the AI strategy off: nothing is asked and nothing is paid.
    """
    if not settings.ai_enabled:
        return None, "ai_disabled"
    key = settings.openai_api_key
    if key is None or not key.get_secret_value().strip():
        return None, "ai_key_missing"
    if settings.ai_total_limit_usd <= 0:
        return None, "ai_spending_cap_is_zero"
    config = AIConfig(model=settings.ai_model)
    if config.model not in OPENAI_REASONING_EFFORT:
        return None, "ai_model_unsupported"
    price = next(
        (
            p
            for p in settings.ai_reviewed_prices
            if (p.provider, p.model) == ("openai", config.model)
        ),
        None,
    )
    if price is None:
        return None, "ai_price_not_reviewed"
    deps = AIDeps(
        provider=HTTPProvider("openai", key),
        price=price,
        budget=BudgetStore(engine, settings),
        config=config,
    )
    return deps, "ai_enabled"


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_database_engine(settings)
    config = LiveConfig()
    ai, ai_note = build_ai(settings, engine)
    journal = ErrorJournal(engine)
    live = LiveEngine(engine, lambda: HttpTransport(), config, ai=ai, journal=journal)
    if arguments.command in ("setup", "cycle", "run"):
        _emit({"ai": ai_note})
    try:
        if arguments.command == "setup":
            await live.setup()
            _emit(await summary(live))
        elif arguments.command == "status":
            _emit(await summary(live))
        elif arguments.command in ("enable", "disable"):
            aliases = {"rules": live.rules_id, "ai": live.ai_id, "benchmark": live.benchmark_id}
            strategy_id = aliases.get(arguments.strategy, arguments.strategy)
            enabled = arguments.command == "enable"
            try:
                changed = await live.store.set_enabled(
                    strategy_id, enabled, actor=f"cli:{_user()}", reason=arguments.reason
                )
            except ValueError:
                _emit({"status": "failed", "error": "unknown strategy", "strategy_id": strategy_id})
                return 2
            _emit({"strategy_id": strategy_id, "enabled": enabled, "changed": changed})
        elif arguments.command == "health":
            verdict = await health(live, live.clock(), timedelta(minutes=arguments.max_age_minutes))
            _emit(verdict)
            return 0 if verdict["healthy"] else 1
        elif arguments.command == "errors":
            if arguments.show:
                group = await journal.group(arguments.show)
                if group is None:
                    _emit({"status": "failed", "error": "no single problem matches that prefix"})
                    return 2
                seen = await journal.occurrences(group.fingerprint)
                _emit(
                    {
                        "problem": group.model_dump(mode="json"),
                        "recent_occurrences": [o.model_dump(mode="json") for o in seen],
                    }
                )
            else:
                found = await journal.groups(
                    status=arguments.status, level=arguments.level, limit=arguments.limit
                )
                _emit(
                    {
                        "summary": (await journal.summary()).model_dump(mode="json"),
                        "problems": [
                            g.model_dump(
                                mode="json", exclude={"last_traceback", "last_context", "location"}
                            )
                            | {"fingerprint": g.fingerprint[:12]}
                            for g in found
                        ],
                    }
                )
        elif arguments.command == "ack":
            target = None if arguments.fingerprint == "all" else arguments.fingerprint
            try:
                count = await journal.acknowledge(
                    target, actor=f"cli:{_user()}", reason=arguments.reason
                )
            except ValueError:
                _emit({"status": "failed", "error": "that prefix matches more than one problem"})
                return 2
            _emit({"acknowledged": count})
        elif arguments.command == "audit":
            rows = await live.store.audit(limit=arguments.limit)
            _emit({"audit": [row.model_dump(mode="json") for row in rows]})
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

                async def record_worker_error(exc: BaseException) -> None:
                    # Strategy failures were already journaled with their detail by the engine.
                    # Integrity failures were journaled by the engine as `paper_broker`.
                    if not isinstance(exc, CycleErrors | LoopIntegrityError):
                        await journal.record_exception("worker", exc, at=live.clock())

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
                    on_error=record_worker_error,
                )
            return 0 if result.failed == 0 else 1
    finally:
        if ai is not None and isinstance(ai.provider, HTTPProvider):
            await ai.provider.aclose()
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
