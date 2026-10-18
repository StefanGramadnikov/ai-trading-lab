"""Synthetic provider contracts: these bodies are examples, not API captures."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import JsonValue

from ai_trading_lab.market_data.adapters.dexscreener import DexScreenerAdapter
from ai_trading_lab.market_data.models import Capture, DataMode, Source, TransportError

MINT = "So11111111111111111111111111111111111111112"
QUOTE = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PAIR = "11111111111111111111111111111111"


def pair() -> dict[str, JsonValue]:
    return {
        "chainId": "solana",
        "dexId": "synthetic",
        "pairAddress": PAIR,
        "baseToken": {"address": MINT},
        "quoteToken": {"address": QUOTE},
        "priceUsd": "0.000000000000000123",
        "liquidity": {"usd": "12.3456789"},
        "pairCreatedAt": 1700000000000,
    }


class Stub:
    def __init__(self, bodies: Sequence[object | TransportError]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict[str, JsonValue] | None]] = []

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        self.calls.append((path, json_body))
        body = self.bodies.pop(0)
        if isinstance(body, TransportError):
            raise body
        if isinstance(body, Capture):
            return body
        return Capture(
            source=source,
            method=method,
            path=path,
            request_body=json_body,
            body=json.dumps(body),
            received_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
            data_mode=DataMode.FIXTURE,
        )


@pytest.mark.asyncio
async def test_precision_units_identity_unknown_time() -> None:
    stub = Stub([[pair()]])
    batch = await DexScreenerAdapter(stub).collect([MINT])
    (observation,) = batch.observations
    assert observation.price == Decimal("0.000000000000000123")
    assert observation.liquidity == Decimal("12.3456789")
    assert observation.price_currency == observation.liquidity_currency == "USD"
    assert observation.instrument.base_asset == MINT
    assert observation.instrument.quote_currency == QUOTE
    assert observation.event_time_utc is None
    assert observation.quality == "PARTIAL"
    assert observation.capture_id == batch.captures[0].capture_id
    assert batch.captures[0].data_mode == DataMode.FIXTURE
    assert not batch.failures


@pytest.mark.asyncio
async def test_quote_seed_preserves_base_price_identity() -> None:
    batch = await DexScreenerAdapter(Stub([[pair()]])).collect([QUOTE])
    assert batch.observations[0].instrument.base_asset == MINT
    assert batch.observations[0].facts["requested_mint"] == QUOTE


@pytest.mark.asyncio
async def test_discovery_filters_deduplicates_and_caps_total_pairs() -> None:
    profiles = [
        {"chainId": "ethereum", "tokenAddress": MINT},
        {},
        {"chainId": "solana", "tokenAddress": MINT},
        {"chainId": "solana", "tokenAddress": MINT},
    ]
    stub = Stub([profiles, [pair(), pair()]])
    batch = await DexScreenerAdapter(stub).collect(limit=2)
    assert len(batch.observations) == 1
    assert len(stub.calls) == 2
    assert batch.failures[0].code == "invalid_profile"
    capped = await DexScreenerAdapter(Stub([[pair(), pair()]])).collect([MINT, QUOTE], limit=1)
    assert len(capped.observations) == len(capped.captures) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("priceUsd", "NaN"),
        ("priceUsd", "Infinity"),
        ("priceUsd", "-1"),
        ("priceUsd", True),
        ("chainId", "ethereum"),
        ("baseToken", {"address": PAIR}),
    ],
)
async def test_reject_invalid_pair(field: str, value: JsonValue) -> None:
    raw = pair()
    raw[field] = value
    batch = await DexScreenerAdapter(Stub([[raw]])).collect([MINT])
    assert not batch.observations
    assert batch.failures[0].code == "invalid_pair"
    assert len(batch.captures) == 1


@pytest.mark.asyncio
async def test_missing_values_are_not_zero() -> None:
    raw = pair()
    raw.pop("priceUsd")
    raw["liquidity"] = None
    batch = await DexScreenerAdapter(Stub([[raw]])).collect([MINT])
    observation = batch.observations[0]
    assert observation.price is None and observation.liquidity is None
    assert "liquidity_missing" in observation.issues


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, None, "bad"])
async def test_invalid_array(body: object) -> None:
    batch = await DexScreenerAdapter(Stub([body])).collect([MINT])
    assert batch.failures[0].code == "invalid_payload"
    assert not batch.observations


@pytest.mark.asyncio
async def test_errors_and_bad_seeds_remain_failures() -> None:
    failed = Capture(
        source=Source.DEXSCREENER,
        method="GET",
        path="/token-profiles/latest/v1",
        body="{}",
        status_code=429,
        received_at_utc=datetime.now(UTC),
    )
    discovery = await DexScreenerAdapter(
        Stub([TransportError(Source.DEXSCREENER, "rate_limited", failed)])
    ).collect()
    assert discovery.captures == (failed,)
    assert discovery.failures[0].code == "rate_limited"
    seeded = await DexScreenerAdapter(
        Stub([TransportError(Source.DEXSCREENER, "timeout")])
    ).collect([MINT])
    assert seeded.failures[0].code == "timeout"
    invalid = await DexScreenerAdapter(Stub([])).collect(["../escape"])
    assert invalid.failures[0].code == "invalid_identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 11, True])
async def test_bounds(limit: int) -> None:
    with pytest.raises(ValueError):
        await DexScreenerAdapter(Stub([])).collect(limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,source,body",
    [
        (503, Source.DEXSCREENER, "[]"),
        (200, Source.SOLANA, "[]"),
        (200, Source.DEXSCREENER, "{bad json"),
    ],
)
async def test_capture_errors(status: int, source: Source, body: str) -> None:
    capture = Capture(
        source=source,
        method="GET",
        path=f"/token-pairs/v1/solana/{MINT}",
        status_code=status,
        body=body,
        received_at_utc=datetime.now(UTC),
    )
    batch = await DexScreenerAdapter(Stub([capture])).collect([MINT])
    assert batch.captures == (capture,)
    assert batch.failures and not batch.observations


@pytest.mark.asyncio
async def test_discovery_cap_and_multiple_seed_budget() -> None:
    profiles = [
        {"chainId": "solana", "tokenAddress": MINT},
        {"chainId": "solana", "tokenAddress": QUOTE},
    ]
    stub = Stub([profiles, [pair()]])
    batch = await DexScreenerAdapter(stub).collect(limit=1)
    assert len(batch.observations) == 1 and len(stub.calls) == 2
