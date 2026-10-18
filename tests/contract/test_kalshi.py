"""Synthetic Kalshi contracts, not live endpoint evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import JsonValue

from ai_trading_lab.market_data.adapters.kalshi import KalshiAdapter
from ai_trading_lab.market_data.models import Capture, DataMode, DataQuality, Source, TransportError


class SyntheticTransport:
    def __init__(
        self,
        replies: list[object],
        *,
        updates: dict[str, object] | None = None,
        update_path: str = "/markets",
    ) -> None:
        self.replies = replies
        self.updates = updates or {}
        self.update_path = update_path
        self.calls: list[str] = []

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        self.calls.append(path)
        reply = self.replies.pop(0)
        if isinstance(reply, TransportError):
            raise reply
        capture = Capture(
            source=source,
            method=method,
            path=path,
            params=params or {},
            body=json.dumps(reply),
            data_mode=DataMode.FIXTURE,
            received_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return capture.model_copy(update=self.updates) if path == self.update_path else capture


def market(cursor: str = "") -> dict[str, object]:
    return {
        "markets": [
            {
                "ticker": "SYNTHETIC-1",
                "market_type": "binary",
                "title": "Title",
                "updated_time": "2026-01-01T00:00:00Z",
            }
        ],
        "cursor": cursor,
    }


def book() -> dict[str, object]:
    return {
        "orderbook_fp": {"yes_dollars": [["0.1234", "12.50"]], "no_dollars": [["0.7000", "3.25"]]}
    }


@pytest.mark.asyncio
async def test_binary_asks_are_derived_and_source_time_unknown() -> None:
    batch = await KalshiAdapter(SyntheticTransport([market(), book()])).collect()
    assert len(batch.observations) == 2
    yes, no = batch.observations
    assert yes.bids[0].price == Decimal("0.1234")
    assert yes.asks[0].price == Decimal("0.3000")
    assert yes.asks[0].quantity == Decimal("3.25")
    assert yes.asks[0].derived and not yes.bids[0].derived
    assert no.asks[0].price == Decimal("0.8766")
    assert yes.event_time_utc is None
    assert yes.quality == DataQuality.PARTIAL
    assert yes.issues == ("source_time_unknown",)
    assert yes.price is None


@pytest.mark.asyncio
async def test_odd_limit_is_total_outcome_bound() -> None:
    transport = SyntheticTransport([market(), book()])
    batch = await KalshiAdapter(transport).collect(limit=1)
    assert len(batch.observations) == 1
    assert len(transport.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "levels", [None, [["NaN", "1"]], [["1.01", "1"]], [["0.1", "-1"]], [[0.1, 1]], [["0.1"]]]
)
async def test_invalid_levels(levels: object) -> None:
    payload = {"orderbook_fp": {"yes_dollars": levels, "no_dollars": []}}
    batch = await KalshiAdapter(SyntheticTransport([market(), payload])).collect()
    assert not batch.observations
    assert batch.failures
    assert len(batch.captures) == 2


@pytest.mark.asyncio
async def test_empty_book_remains_partial() -> None:
    payload: dict[str, object] = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    batch = await KalshiAdapter(SyntheticTransport([market(), payload])).collect()
    assert "empty_book" in batch.observations[0].issues


@pytest.mark.asyncio
async def test_repeat_cursor_and_market_are_reported() -> None:
    batch = await KalshiAdapter(
        SyntheticTransport([market("next"), book(), market("next")])
    ).collect()
    assert {f.code for f in batch.failures} == {"repeated_market", "repeated_cursor"}


@pytest.mark.asyncio
async def test_page_cap_and_transport_failure() -> None:
    batch = await KalshiAdapter(
        SyntheticTransport([market("next"), TransportError(Source.KALSHI, "http_429")])
    ).collect(max_pages=1)
    assert {f.code for f in batch.failures} == {"http_429", "page_limit_reached"}


@pytest.mark.asyncio
async def test_unsupported_market_type() -> None:
    batch = await KalshiAdapter(
        SyntheticTransport([{"markets": [{"ticker": "SCALAR", "market_type": "scalar"}]}])
    ).collect()
    assert batch.failures[0].code == "unsupported_market_type"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {"markets": None}, {"markets": [], "cursor": "next"}])
async def test_bad_or_empty_pages(payload: object) -> None:
    batch = await KalshiAdapter(SyntheticTransport([payload])).collect()
    assert not batch.observations
    assert batch.failures


@pytest.mark.asyncio
async def test_bad_collection_bounds() -> None:
    with pytest.raises(ValueError):
        await KalshiAdapter(SyntheticTransport([])).collect(max_pages=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,max_pages", [(True, 1), (1, True), (11, 1), (1, 4)])
async def test_collection_bounds_reject_bool_and_excess(limit: int, max_pages: int) -> None:
    with pytest.raises(ValueError):
        await KalshiAdapter(SyntheticTransport([])).collect(limit=limit, max_pages=max_pages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"source": Source.SOLANA},
        {"path": "/wrong"},
        {"method": "POST"},
        {"params": {}},
        {"status_code": 429},
        {"request_body": {}},
    ],
)
@pytest.mark.parametrize("stage", ["metadata", "book"])
async def test_returned_capture_identity(updates: dict[str, object], stage: str) -> None:
    path = "/markets/SYNTHETIC-1/orderbook" if stage == "book" else "/markets"
    transport = SyntheticTransport([market(), book()], updates=updates, update_path=path)
    batch = await KalshiAdapter(transport).collect(limit=1)
    assert not batch.observations
    assert batch.failures
    assert len(batch.captures) == (2 if stage == "book" else 1)


@pytest.mark.asyncio
async def test_high_precision_complement_ignores_ambient_precision() -> None:
    from decimal import localcontext

    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.123456789012345678901234567890123456789", "1"]],
            "no_dollars": [],
        }
    }
    with localcontext() as context:
        context.prec = 4
        batch = await KalshiAdapter(SyntheticTransport([market(), payload])).collect()
    assert batch.observations[1].asks[0].price == Decimal(
        "0.876543210987654321098765432109876543211"
    )


@pytest.mark.asyncio
async def test_does_not_fetch_another_market_book_when_full() -> None:
    metadata = {
        "markets": [
            {"ticker": "SYNTHETIC-1", "market_type": "binary"},
            {"ticker": "SYNTHETIC-2", "market_type": "binary"},
        ]
    }
    transport = SyntheticTransport([metadata, book()])
    batch = await KalshiAdapter(transport).collect(limit=2)
    assert len(batch.observations) == 2
    assert transport.calls == ["/markets", "/markets/SYNTHETIC-1/orderbook"]


@pytest.mark.asyncio
async def test_extreme_decimal_scale_is_rejected_before_complement() -> None:
    payload = {"orderbook_fp": {"yes_dollars": [["1e-100000000", "1"]], "no_dollars": []}}
    batch = await KalshiAdapter(SyntheticTransport([market(), payload])).collect()
    assert not batch.observations
    assert batch.failures
