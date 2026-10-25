from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.market_data.collector import collect, replay
from ai_trading_lab.market_data.models import Batch, Source, TransportError
from ai_trading_lab.market_data.replay import CaptureArchive
from ai_trading_lab.market_data.storage import ObservationStore
from ai_trading_lab.market_data.transport import HttpTransport


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Bounded public market data; PAPER_ONLY, no orders"
    )
    commands = result.add_subparsers(dest="command", required=True)
    live = commands.add_parser(
        "collect", help="Explicit bounded public-data collection, persisted to PostgreSQL"
    )
    live.add_argument("--source", type=Source, choices=list(Source), required=True)
    live.add_argument("--mint", action="append", default=[])
    live.add_argument(
        "--pair",
        action="append",
        default=[],
        help="Kraken trading pair such as XBTUSD (repeatable); stored with the explicit-ID list",
    )
    live.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    live.add_argument("--max-pages", type=int, choices=range(1, 4), default=2)
    live.add_argument("--capture-file", type=Path)
    offline = commands.add_parser(
        "replay", help="Offline replay and persistence; no public networking"
    )
    offline.add_argument("archive", type=Path)
    return result


def read_archive(path: Path) -> CaptureArchive:
    with path.open("rb") as handle:
        content = handle.read(8_000_001)
    if len(content) > 8_000_000:
        raise ValueError("Archive exceeds 8MB replay input bound")
    return CaptureArchive.model_validate_json(content)


def write_archive(path: Path, archive: CaptureArchive) -> None:
    content = archive.model_dump_json(indent=2)
    if len(content.encode("utf-8")) > 8_000_000:
        raise ValueError("Archive exceeds 8MB output bound")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    batch: Batch
    if arguments.command == "collect":
        output: Path | None = arguments.capture_file
        if output is not None and await asyncio.to_thread(output.exists):
            raise ValueError("Capture output already exists; refusing overwrite")
        if arguments.pair and Source(arguments.source) != Source.KRAKEN:
            raise ValueError("--pair is only valid with --source kraken")
        identifiers = [*arguments.mint, *arguments.pair]
        if len(identifiers) > 10:
            raise ValueError("At most ten mints or pairs are allowed")
        source = Source(arguments.source)
        async with HttpTransport() as transport:
            batch = await collect(
                source,
                transport,
                mint_addresses=identifiers,
                limit=arguments.limit,
                max_pages=arguments.max_pages,
            )
        if output is not None:
            archive = CaptureArchive(
                source=source,
                mint_addresses=tuple(identifiers),
                limit=arguments.limit,
                max_pages=arguments.max_pages,
                captures=batch.captures,
            )
            await asyncio.to_thread(write_archive, output, archive)
    else:
        archive_path = Path(arguments.archive)
        archive = await asyncio.to_thread(read_archive, archive_path)
        source = archive.source
        batch = await replay(archive)
    engine = create_database_engine(settings)
    try:
        summary = await ObservationStore(engine).save(source, batch)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                **asdict(summary),
                "run_id": str(summary.run_id),
                "source": source.value,
                "mode": "REPLAY"
                if arguments.command == "replay"
                else (
                    next(iter({c.data_mode.value for c in batch.captures}))
                    if len({c.data_mode for c in batch.captures}) == 1
                    else "UNKNOWN"
                ),
                "failure_codes": [f.code for f in batch.failures],
            }
        )
    )
    return 1 if batch.failures else 0


def main() -> None:
    arguments = parser().parse_args()
    try:
        result = asyncio.run(run(arguments))
    except (ValidationError, SQLAlchemyError, OSError, ValueError, TransportError):
        # External raw bodies and database connection strings never reach CLI errors.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Invalid input, unavailable source or persistence failure",
                }
            )
        )
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
