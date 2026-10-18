"""Synthetic contract evidence, never captured market or performance evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import JsonValue

from ai_trading_lab.market_data.adapters.polymarket import PolymarketAdapter
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
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        self.calls.append((path, params))
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


def market() -> list[dict[str, object]]:
    return [
        {
            "id": "1",
            "conditionId": "condition",
            "question": "Untrusted title",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": '["100","200"]',
        }
    ]


def book() -> dict[str, object]:
    return {
        "asset_id": "100",
        "market": "condition",
        "timestamp": "1767225600000",
        "bids": [{"price": "0.1234567890123456789", "size": "2.5"}],
        "asks": [],
    }


@pytest.mark.asyncio
async def test_precise_book_and_total_bound() -> None:
    transport = SyntheticTransport([market(), book()])
    batch = await PolymarketAdapter(transport).collect(limit=1)
    assert len(batch.observations) == 1
    item = batch.observations[0]
    assert item.bids[0].price == Decimal("0.1234567890123456789")
    assert item.price is None
    assert item.quality == DataQuality.VALID
    assert item.instrument.native_id == "100"
    assert item.instrument.quote_currency is None
    assert len(transport.calls) == 2
    assert not batch.failures


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("asset_id", "999"),
        ("market", "wrong"),
        ("timestamp", "NaN"),
        ("bids", [{"price": "1.1", "size": "1"}]),
        ("bids", [{"price": "0.1", "size": "-1"}]),
        ("bids", None),
    ],
)
async def test_invalid_book(field: str, value: object) -> None:
    payload = book()
    payload[field] = value
    batch = await PolymarketAdapter(SyntheticTransport([market(), payload])).collect(limit=1)
    assert not batch.observations
    assert batch.failures
    assert len(batch.captures) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", ['["100"]', '["100","100"]', "null"])
async def test_bad_alignment(tokens: str) -> None:
    metadata = market()
    metadata[0]["clobTokenIds"] = tokens
    batch = await PolymarketAdapter(SyntheticTransport([metadata])).collect(limit=1, max_pages=1)
    assert not batch.observations
    assert batch.failures[0].code == "malformed_market"


@pytest.mark.asyncio
async def test_empty_book_and_stale_time() -> None:
    payload = book()
    payload.update(bids=[], timestamp="1767225400000")
    batch = await PolymarketAdapter(SyntheticTransport([market(), payload])).collect(limit=1)
    assert batch.observations[0].quality == DataQuality.STALE
    assert "empty_book" in batch.observations[0].issues


@pytest.mark.asyncio
async def test_transport_failure_preserved() -> None:
    batch = await PolymarketAdapter(
        SyntheticTransport([TransportError(Source.POLYMARKET, "http_429")])
    ).collect()
    assert batch.failures[0].code == "http_429"
    assert not batch.observations


@pytest.mark.asyncio
async def test_repeated_markets_and_page_cap() -> None:
    transport = SyntheticTransport(
        [
            market(),
            TransportError(Source.POLYMARKET, "http_500"),
            TransportError(Source.POLYMARKET, "http_500"),
            market(),
        ]
    )
    batch = await PolymarketAdapter(transport).collect(limit=3)
    assert {f.code for f in batch.failures} >= {"http_500", "repeated_market", "page_limit_reached"}
    assert len(transport.calls) == 4


@pytest.mark.asyncio
async def test_empty_metadata_is_explicit() -> None:
    batch = await PolymarketAdapter(SyntheticTransport([[]])).collect()
    assert batch.failures[0].code == "empty_page"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp,quality,issue",
    [
        (None, DataQuality.PARTIAL, "source_time_unknown"),
        ("1767225700000", DataQuality.INVALID, "source_time_in_future"),
    ],
)
async def test_missing_and_future_timestamps(
    timestamp: str | None, quality: DataQuality, issue: str
) -> None:
    payload = book()
    payload["timestamp"] = timestamp
    batch = await PolymarketAdapter(SyntheticTransport([market(), payload])).collect(limit=1)
    assert batch.observations[0].quality == quality
    assert issue in batch.observations[0].issues


@pytest.mark.asyncio
async def test_bad_collection_bounds() -> None:
    with pytest.raises(ValueError):
        await PolymarketAdapter(SyntheticTransport([])).collect(limit=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,max_pages", [(True, 1), (1, True), (11, 1), (1, 4)])
async def test_collection_bounds_reject_bool_and_excess(limit: int, max_pages: int) -> None:
    with pytest.raises(ValueError):
        await PolymarketAdapter(SyntheticTransport([])).collect(limit=limit, max_pages=max_pages)


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
    path = "/book" if stage == "book" else "/markets"
    transport = SyntheticTransport([market(), book()], updates=updates, update_path=path)
    batch = await PolymarketAdapter(transport).collect(limit=1)
    assert not batch.observations
    assert batch.failures
    assert len(batch.captures) == (2 if stage == "book" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp", ["1e1000000", "-1e1000000", "1e-1000000", "253402300800000", "-62135596800001"]
)
async def test_extreme_finite_timestamp_retains_other_book_evidence(timestamp: str) -> None:
    invalid_book = book()
    invalid_book["timestamp"] = timestamp
    valid_book = book()
    valid_book["asset_id"] = "200"
    batch = await PolymarketAdapter(
        SyntheticTransport([market(), invalid_book, valid_book])
    ).collect(limit=2, max_pages=1)
    assert len(batch.captures) == 3
    assert len(batch.observations) == 1
    assert batch.observations[0].instrument.native_id == "200"
    assert any(f.code == "malformed_book" and f.target == "100" for f in batch.failures)


@pytest.mark.asyncio
async def test_timestamp_exact_under_low_decimal_precision() -> None:
    from decimal import localcontext

    payload = book()
    payload["timestamp"] = "1767225600123.456"
    with localcontext() as context:
        context.prec = 4
        batch = await PolymarketAdapter(SyntheticTransport([market(), payload])).collect(limit=1)
    assert batch.observations[0].event_time_utc == datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["outcomes", "clobTokenIds"])
async def test_excessively_nested_encoded_array_keeps_prior_evidence(field: str) -> None:
    good = market()[0]
    bad = {**good, "id": "2", "conditionId": "second", field: "[" * 10000 + "0" + "]" * 10000}
    transport = SyntheticTransport([[good, bad], book(), {**book(), "asset_id": "200"}])
    batch = await PolymarketAdapter(transport).collect(limit=3, max_pages=1)
    assert len(batch.observations) == 2
    assert len(batch.captures) == 3
    assert [failure.code for failure in batch.failures] == [
        "malformed_market",
        "page_limit_reached",
    ]


@pytest.mark.asyncio
async def test_nested_raw_book_keeps_prior_observation_and_capture() -> None:
    class NestedBookTransport(SyntheticTransport):
        async def request(
            self,
            source: Source,
            method: Literal["GET", "POST"],
            path: str,
            *,
            params: dict[str, str] | None = None,
            json_body: dict[str, JsonValue] | None = None,
        ) -> Capture:
            capture = await super().request(
                source, method, path, params=params, json_body=json_body
            )
            if params == {"token_id": "200"}:
                return capture.model_copy(update={"body": "[" * 10000 + "0" + "]" * 10000})
            return capture

    transport = NestedBookTransport([market(), book(), {**book(), "asset_id": "200"}])
    batch = await PolymarketAdapter(transport).collect(limit=2, max_pages=1)
    assert len(batch.observations) == 1
    assert len(batch.captures) == 3
    assert [failure.code for failure in batch.failures] == ["malformed_book", "page_limit_reached"]

    from ai_trading_lab.market_data.collector import replay
    from ai_trading_lab.market_data.replay import CaptureArchive

    replayed = await replay(
        CaptureArchive(source=Source.POLYMARKET, limit=2, max_pages=1, captures=batch.captures)
    )
    assert len(replayed.observations) == 1
    assert len(replayed.captures) == 3
    assert [failure.code for failure in replayed.failures] == [
        "malformed_book",
        "page_limit_reached",
    ]
