"""A real read-only Kraken capture (2026-10-25T14:19Z, BTC/ETH/SOL against USD).

Guards the adapter against the venue's actual shapes, which differed from the first synthetic
assumptions in three ways: fee schedules are returned empty, a candle window holds 721 rows
(720 closed plus the one still forming), and tracking cookies are set on the first response.
Only the candle rows were trimmed (to the last 6 of 721) for size; everything else is verbatim.
Market data, not trading evidence: it says nothing about profitability.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ai_trading_lab.market_data.cli import read_archive
from ai_trading_lab.market_data.collector import replay
from ai_trading_lab.market_data.models import DataMode, DataQuality, Source

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kraken_real_sample.json"


@pytest.mark.asyncio
async def test_real_capture_replays_to_three_valid_spot_observations() -> None:
    batch = await replay(read_archive(FIXTURE))
    assert not batch.failures
    assert {c.data_mode for c in batch.captures} == {DataMode.REPLAY}
    assert [o.instrument.native_id for o in batch.observations] == [
        "XXBTZUSD",
        "XETHZUSD",
        "SOLUSD",
    ]
    for observation in batch.observations:
        assert observation.quality == DataQuality.VALID and not observation.issues
        assert observation.instrument.source == Source.KRAKEN
        assert observation.instrument.kind == "spot"
        assert observation.price_currency == "USD" and observation.price is not None
        # Best-first on both sides, never crossed, one real tick wide or more.
        bids, asks = observation.bids, observation.asks
        assert len(bids) == len(asks) == 25
        assert [b.price for b in bids] == sorted((b.price for b in bids), reverse=True)
        assert [a.price for a in asks] == sorted(a.price for a in asks)
        assert 0 < asks[0].price - bids[0].price < asks[0].price / 100
        # The last trade sits inside or next to the touch.
        assert bids[0].price * Decimal("0.99") < observation.price < asks[0].price * Decimal("1.01")
        # Event time is Kraken's server time; receipt follows it by well under the 60 s limit.
        assert observation.event_time_utc is not None
        skew = (observation.received_at_utc - observation.event_time_utc).total_seconds()
        assert 0 <= skew < 5
        assert observation.event_time_utc.date() == datetime(2026, 10, 25, tzinfo=UTC).date()
        meta = observation.instrument.metadata
        assert Decimal(str(meta["tick_size"])) > 0 and Decimal(str(meta["ordermin"])) > 0
        assert meta["status"] == "online"
        assert meta["fees_published"] is False and "fees_taker" not in meta
        candle = observation.facts["last_closed_candle"]
        assert isinstance(candle, dict) and candle["interval_minutes"] == 15
        # The candle forming at capture time (14:15-14:30) must never be reported as closed.
        assert str(candle["close_time_utc"]) <= "2026-10-25T14:15:00+00:00"
