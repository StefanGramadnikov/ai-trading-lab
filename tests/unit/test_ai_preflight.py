"""Local rejection precedes spending; sent failures keep unknown billing."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr

from ai_trading_lab.ai.budget import BudgetStore, Reservation
from ai_trading_lab.ai.models import PriceSnapshot
from ai_trading_lab.ai.providers import HTTPProvider
from ai_trading_lab.ai.service import InferenceService
from tests.contract.test_ai_providers import request


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["input", "unicode", "provider", "model", "header"])
async def test_f09_local_preflight_never_reserves_or_sends(failure: str) -> None:
    req = request()
    if failure == "input":
        req = request(max_input_tokens=1)
    elif failure == "unicode":
        req = request(evidence=req.evidence.model_dump() | {"payload": "🐍" * 16000})
    elif failure == "provider":
        req = request("anthropic")
    elif failure == "model":
        req = request("xai", model="unsupported-model")
    key = "synthetic\ninvalid" if failure == "header" else "synthetic"
    store = AsyncMock(spec=BudgetStore)
    store.reserve.return_value = Reservation(status="new", reason="reserved", reserved_amount="1")
    store.finish.side_effect = lambda _budget, _call, _fingerprint, result: result
    sent = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        sent += 1
        return httpx2.Response(400)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = HTTPProvider("xai" if failure == "model" else "openai", SecretStr(key), client)
        price = PriceSnapshot(
            provider=req.provider,
            model=req.model,
            input_per_million="1",
            output_per_million="1",
            source_url="fixture://synthetic",
            verified_at_utc=req.evidence.available_at_utc,
            version="synthetic-v1",
        )
        result = await InferenceService(store, provider).call(
            uuid4(), uuid4(), Decimal("1"), req, price, req.evidence.available_at_utc
        )
    assert result.status == "invalid"
    assert sent == 0
    store.reserve.assert_not_awaited()
    store.finish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "400", "429", "500"])
async def test_f09_sent_failure_does_not_invent_zero_billing(failure: str) -> None:
    req = request()
    store = AsyncMock(spec=BudgetStore)
    store.reserve.return_value = Reservation(status="new", reason="reserved", reserved_amount="1")
    store.finish.side_effect = lambda _budget, _call, _fingerprint, result: result
    sent = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        sent += 1
        if failure == "timeout":
            raise httpx2.ReadTimeout("synthetic")
        return httpx2.Response(int(failure))

    price = PriceSnapshot(
        provider=req.provider,
        model=req.model,
        input_per_million="1",
        output_per_million="1",
        source_url="fixture://synthetic",
        verified_at_utc=req.evidence.available_at_utc,
        version="synthetic-v1",
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        result = await InferenceService(
            store, HTTPProvider("openai", SecretStr("synthetic"), client)
        ).call(uuid4(), uuid4(), Decimal("1"), req, price, req.evidence.available_at_utc)
    assert sent == 1
    assert result.usage is None
    store.reserve.assert_awaited_once()
    store.finish.assert_awaited_once()
    assert store.finish.call_args.args[-1].usage is None
