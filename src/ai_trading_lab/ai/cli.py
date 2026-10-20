"""Explicit fixture runs and opt-in real-provider integration smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.ai.providers import FixtureProvider, HTTPProvider, Provider
from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.ai.run_store import AIRunStore
from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.market_data.models import parse_json


def read_run(path: Path) -> AIRunInput:
    with path.open("rb") as handle:
        content = handle.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError("AI run exceeds 2MB")
    return AIRunInput.model_validate(parse_json(content.decode()))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Bounded PAPER_ONLY AI research")
    commands = result.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("scenario", type=Path)
    commands.add_parser("doctor")
    return result


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if arguments.command == "doctor":
        print(
            json.dumps(
                {
                    "execution_mode": settings.execution_mode,
                    "ai_enabled": settings.ai_enabled,
                    "openai_key_configured": bool(
                        settings.openai_api_key
                        and settings.openai_api_key.get_secret_value().strip()
                    ),
                }
            )
        )
        return 0
    spec = await asyncio.to_thread(read_run, Path(arguments.scenario))
    provider: Provider
    if spec.inference_mode == "real":
        if not settings.ai_enabled:
            raise ValueError("AI activation is disabled")
        key = {
            "openai": settings.openai_api_key,
            "xai": settings.xai_api_key,
            "anthropic": settings.anthropic_api_key,
        }.get(spec.provider)
        if key is None or not key.get_secret_value().strip():
            raise ValueError("Personal provider key is not configured")
        provider = HTTPProvider(spec.provider, key)
    else:
        provider = FixtureProvider(spec.fixture_results)
    engine = create_database_engine(settings)
    try:
        output = await AIRunStore(engine, provider).run(spec)
    finally:
        if isinstance(provider, HTTPProvider):
            await provider.aclose()
        await engine.dispose()
    print(json.dumps(output, sort_keys=True))
    return 0


def main() -> None:
    args = parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except (ValueError, OSError, TimeoutError, SQLAlchemyError):
        print(
            json.dumps(
                {"status": "failed", "error": "Invalid input, configuration, budget or persistence"}
            )
        )
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
