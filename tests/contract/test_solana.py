"""Synthetic Solana JSON-RPC contracts, never observed API evidence."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import JsonValue

from ai_trading_lab.market_data.adapters.solana import SolanaAdapter
from ai_trading_lab.market_data.models import Capture, DataMode, Source, TransportError

MINT = "So11111111111111111111111111111111111111112"
PAIR = "11111111111111111111111111111111"


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


def response(method: str, *, amount: str = "18446744073709551615") -> dict[str, JsonValue]:
    value: JsonValue = {"amount": amount, "decimals": 9, "uiAmount": 1.0}
    if method == "getTokenLargestAccounts":
        value = [{"address": PAIR, "amount": amount, "decimals": 9}]
    return {"jsonrpc": "2.0", "id": method, "result": {"context": {"slot": 1234}, "value": value}}


@pytest.mark.asyncio
async def test_preserves_raw_amounts_slot_and_no_holder_inference() -> None:
    stub = Stub([response("getTokenSupply"), response("getTokenLargestAccounts")])
    batch = await SolanaAdapter(stub).collect([MINT])
    assert len(batch.observations) == 2
    assert batch.observations[0].facts["raw_amount"] == "18446744073709551615"
    assert batch.observations[1].facts["token_accounts"] == [
        {"address": PAIR, "raw_amount": "18446744073709551615", "decimals": 9}
    ]
    for observation, capture in zip(batch.observations, batch.captures, strict=True):
        assert observation.facts["context_slot"] == 1234
        assert observation.price is None and observation.event_time_utc is None
        assert observation.quality == "PARTIAL"
        assert observation.capture_id == capture.capture_id
        assert capture.data_mode == DataMode.FIXTURE
        assert capture.request_body is not None
        assert capture.request_body["params"] == [MINT, {"commitment": "finalized"}]
    assert not batch.failures


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["-1", "NaN", "1.5", "18446744073709551616"])
async def test_invalid_raw_amounts(amount: str) -> None:
    batch = await SolanaAdapter(
        Stub(
            [
                response("getTokenSupply", amount=amount),
                response("getTokenLargestAccounts", amount=amount),
            ]
        )
    ).collect([MINT])
    assert len(batch.failures) == len(batch.captures) == 2
    assert not batch.observations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"jsonrpc": "2.0", "id": "wrong"},
        {"jsonrpc": "2.0", "id": "getTokenSupply", "error": {"code": -1}},
        None,
    ],
)
async def test_malformed_and_rpc_errors_preserve_other_result(body: object) -> None:
    batch = await SolanaAdapter(Stub([body, response("getTokenLargestAccounts")])).collect([MINT])
    assert len(batch.observations) == len(batch.failures) == 1
    assert len(batch.captures) == 2


@pytest.mark.asyncio
async def test_missing_mints_invalid_identity_and_limits() -> None:
    missing = await SolanaAdapter(Stub([])).collect()
    assert missing.failures[0].code == "explicit_mints_required"
    invalid = await SolanaAdapter(Stub([])).collect(["bad"])
    assert invalid.failures[0].code == "invalid_identity"
    stub = Stub([response("getTokenSupply"), response("getTokenLargestAccounts")])
    batch = await SolanaAdapter(stub).collect([MINT, PAIR], limit=1)
    assert len(batch.observations) == len(stub.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 11, True])
async def test_bad_limits(limit: int) -> None:
    with pytest.raises(ValueError):
        await SolanaAdapter(Stub([])).collect(limit=limit)


@pytest.mark.asyncio
async def test_transport_failure_retains_capture_and_partial_success() -> None:
    failed = Capture(
        data_mode="FIXTURE",
        source=Source.SOLANA,
        method="POST",
        path="/",
        body="{}",
        status_code=503,
        received_at_utc=datetime.now(UTC),
    )
    batch = await SolanaAdapter(
        Stub(
            [
                TransportError(Source.SOLANA, "http_error", failed),
                response("getTokenLargestAccounts"),
            ]
        )
    ).collect([MINT])
    assert batch.captures[0] == failed
    assert batch.failures[0].code == "http_error"
    assert len(batch.observations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        [{"address": PAIR, "amount": "1", "decimals": 9}] * 2,
        [
            {"address": PAIR, "amount": "1", "decimals": 9},
            {"address": MINT, "amount": "2", "decimals": 8},
        ],
        [{"address": PAIR, "amount": "1", "decimals": 9}] * 21,
    ],
)
async def test_inconsistent_accounts(values: list[JsonValue]) -> None:
    body: dict[str, JsonValue] = {
        "jsonrpc": "2.0",
        "id": "getTokenLargestAccounts",
        "result": {"context": {"slot": 1}, "value": values},
    }
    batch = await SolanaAdapter(Stub([response("getTokenSupply"), body])).collect([MINT])
    assert len(batch.observations) == len(batch.failures) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,source", [(503, Source.SOLANA), (200, Source.DEXSCREENER)])
async def test_capture_validation(status: int, source: Source) -> None:
    capture = Capture(
        data_mode="FIXTURE",
        source=source,
        method="POST",
        path="/",
        status_code=status,
        body="{}",
        request_body={
            "jsonrpc": "2.0",
            "id": "getTokenSupply",
            "method": "getTokenSupply",
            "params": [MINT, {"commitment": "finalized"}],
        },
        received_at_utc=datetime.now(UTC),
    )
    batch = await SolanaAdapter(Stub([capture, response("getTokenLargestAccounts")])).collect(
        [MINT]
    )
    assert batch.captures[0] == capture
    assert len(batch.failures) == len(batch.observations) == 1
