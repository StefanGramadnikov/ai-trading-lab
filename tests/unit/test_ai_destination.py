"""The provider key only goes to a publicly routable address, and a refusal costs nothing."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.models import PriceSnapshot
from ai_trading_lab.ai.providers import HTTPProvider, _peer_is_public
from ai_trading_lab.ai.service import InferenceService
from tests.contract.test_ai_providers import request


def resolver(*addresses: str):  # type: ignore[no-untyped-def]
    async def resolve(_host: str) -> list[str]:
        return list(addresses)

    return resolve


def price(req) -> PriceSnapshot:  # type: ignore[no-untyped-def]
    return PriceSnapshot(
        provider=req.provider,
        model=req.model,
        input_per_million="1",
        output_per_million="1",
        source_url="fixture://synthetic",
        verified_at_utc=req.evidence.available_at_utc,
        version="synthetic-v1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["::1"],
        ["::ffff:192.168.1.1"],
        ["93.184.216.34", "127.0.0.1"],  # one bad answer among good ones is still refused
        [],
    ],
)
async def test_a_host_that_resolves_to_a_non_public_address_is_refused(
    addresses: list[str],
) -> None:
    provider = HTTPProvider("openai", SecretStr("synthetic"), resolver=resolver(*addresses))
    refused = await provider.destination(request())
    assert refused is not None
    assert (refused.status, refused.reason) == ("invalid", "unsafe_destination")


@pytest.mark.asyncio
async def test_public_addresses_pass_and_an_unresolvable_host_is_refused_locally() -> None:
    ok = HTTPProvider("openai", SecretStr("synthetic"), resolver=resolver("93.184.216.34"))
    assert await ok.destination(request()) is None

    async def broken(_host: str) -> list[str]:
        raise OSError("dns down")

    down = HTTPProvider("openai", SecretStr("synthetic"), resolver=broken)
    refused = await down.destination(request())
    assert refused is not None and refused.reason == "destination_unresolvable"


@pytest.mark.asyncio
async def test_a_controlled_transport_is_not_resolved_unless_asked() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(400))
    ) as c:
        provider = HTTPProvider("openai", SecretStr("synthetic"), c)
        assert await provider.destination(request()) is None


@pytest.mark.asyncio
async def test_the_service_refuses_before_reserving_or_sending_anything() -> None:
    req = request()
    store = AsyncMock(spec=BudgetStore)
    sent = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        sent += 1
        return httpx2.Response(400)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = HTTPProvider(
            "openai", SecretStr("synthetic"), client, resolver=resolver("192.168.0.10")
        )
        result = await InferenceService(store, provider).call(
            uuid4(), uuid4(), Decimal("1"), req, price(req), req.evidence.available_at_utc
        )
    assert (result.status, result.reason) == ("invalid", "unsafe_destination")
    assert sent == 0
    store.reserve.assert_not_awaited()
    store.finish.assert_not_awaited()


class _Stream:
    def __init__(self, peer: object) -> None:
        self.peer = peer

    def get_extra_info(self, name: str) -> object:
        return self.peer if name == "server_addr" else None


def _response(peer: object | None) -> httpx2.Response:
    extensions = {} if peer is None else {"network_stream": _Stream(peer)}
    return httpx2.Response(200, extensions=extensions)


def test_the_peer_that_actually_answered_must_be_public() -> None:
    assert _peer_is_public(_response(("93.184.216.34", 443))) is True
    assert _peer_is_public(_response(("127.0.0.1", 443))) is False
    assert _peer_is_public(_response(("10.1.2.3", 443))) is False
    assert _peer_is_public(_response("not-a-tuple")) is False
    assert _peer_is_public(_response(None)) is True  # an in-process transport has no socket


@pytest.mark.asyncio
async def test_a_private_peer_after_connecting_is_an_unknown_outcome_never_a_success() -> None:
    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, extensions={"network_stream": _Stream(("10.0.0.9", 443))})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = HTTPProvider(
            "openai", SecretStr("synthetic"), client, resolver=resolver("93.184.216.34")
        )
        result = await provider.analyze(request())
    assert (result.status, result.reason) == ("unknown", "unsafe_peer_unknown_billing")
