"""Two explicit read-only requests; raw native evidence remains in each runtime event."""

from collections.abc import Callable
from datetime import UTC, datetime

from ai_trading_lab.market_data.models import DataMode, Source, Transport
from ai_trading_lab.paper_execution.replay import ReplayEntry
from ai_trading_lab.runtime.evidence import observed_quote
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.strategies.models import StrategyFrame


async def collect_frame(
    config: RuntimeConfig,
    transport: Transport,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    config = RuntimeConfig.model_validate(config.model_dump(mode="json"))
    instrument = config.instrument
    if config.data_mode != DataMode.OBSERVED or instrument is None:
        raise ValueError("Original observed session and explicit instrument required")
    if instrument.source != Source.POLYMARKET or not instrument.market_id:
        raise ValueError("Source lacks qualified runtime execution evidence")
    metadata = await transport.request(
        Source.POLYMARKET,
        "GET",
        "/markets",
        params={
            "condition_ids": instrument.market_id,
            "limit": "1",
            "active": "true",
            "closed": "false",
        },
    )
    book = await transport.request(
        Source.POLYMARKET, "GET", "/book", params={"token_id": instrument.native_id}
    )
    entry = ReplayEntry(
        metadata_capture=metadata,
        book_capture=book,
        instrument=instrument,
        assumption_version=config.simulation.version,
    )
    # No forecast is manufactured from a quote or an LLM's current knowledge.
    frame = StrategyFrame(
        quote=observed_quote(entry),
        at_utc=clock(),
        resolution_terms_hash=entry.resolution_terms_hash,
    )
    return {
        "action": "tick",
        "frame": frame.model_dump(mode="json"),
        "source_entry": entry.model_dump(mode="json"),
    }
