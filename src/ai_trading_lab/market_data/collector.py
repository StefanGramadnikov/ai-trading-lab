from collections.abc import Sequence

from ai_trading_lab.market_data.adapters.dexscreener import DexScreenerAdapter
from ai_trading_lab.market_data.adapters.kalshi import KalshiAdapter
from ai_trading_lab.market_data.adapters.kraken import DEFAULT_PAIRS, KrakenAdapter
from ai_trading_lab.market_data.adapters.polymarket import PolymarketAdapter
from ai_trading_lab.market_data.adapters.solana import SolanaAdapter
from ai_trading_lab.market_data.models import Batch, Failure, Source, Transport
from ai_trading_lab.market_data.replay import CaptureArchive, ReplayTransport


async def collect(
    source: Source,
    transport: Transport,
    *,
    mint_addresses: Sequence[str] = (),
    limit: int = 5,
    max_pages: int = 2,
) -> Batch:
    if (
        isinstance(limit, bool)
        or not 1 <= limit <= 10
        or isinstance(max_pages, bool)
        or not 1 <= max_pages <= 3
    ):
        raise ValueError("Collection limits outside approved bounded range")
    if len(mint_addresses) > 10:
        raise ValueError("At most ten explicit mints may be supplied")
    if source == Source.DEXSCREENER:
        batch = await DexScreenerAdapter(transport).collect(mint_addresses, limit=limit)
    elif source == Source.SOLANA:
        batch = await SolanaAdapter(transport).collect(mint_addresses, limit=limit)
    elif source == Source.POLYMARKET:
        batch = await PolymarketAdapter(transport).collect(limit=limit, max_pages=max_pages)
    elif source == Source.KRAKEN:
        # For Kraken the explicit list carries trading pairs (e.g. XBTUSD), not Solana mints.
        pairs = tuple(mint_addresses) or DEFAULT_PAIRS
        batch = await KrakenAdapter(transport).collect(pairs)
    else:
        batch = await KalshiAdapter(transport).collect(limit=limit, max_pages=max_pages)
    return batch.model_copy(
        update={"observations": tuple(o.assessed() for o in batch.observations)}
    )


async def replay(archive: CaptureArchive) -> Batch:
    transport = ReplayTransport(archive)
    batch = await collect(
        archive.source,
        transport,
        mint_addresses=archive.mint_addresses,
        limit=archive.limit,
        max_pages=archive.max_pages,
    )
    if transport.remaining:
        failure = Failure(
            source=archive.source,
            target="archive",
            code="replay_unconsumed",
            message="Archive contains responses not consumed by the collection contract",
        )
        return batch.model_copy(update={"failures": (*batch.failures, failure)})
    return batch
