"""OBSERVED provenance requires a public destination: no loopback/private/spoofed hosts."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from ai_trading_lab.market_data.models import DataMode, Source, TransportError
from ai_trading_lab.market_data.transport import HttpTransport, TransportLimits, is_public_address

PUBLIC_V4, PUBLIC_V6 = "104.20.23.154", "2606:4700:10::6814:179a"


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "8.8.8.8"])
def test_public_addresses_are_accepted(address: str) -> None:
    assert is_public_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",
        "fe80::1",
        "fc00::1",
        "224.0.0.1",
        "192.0.2.1",  # documentation range
        "not-an-ip",
        "",
    ],
)
def test_loopback_private_reserved_and_malformed_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


class Stream:
    """Stands in for the connection httpx2 exposes on a real response."""

    def __init__(self, address: str | None) -> None:
        self.address = address

    def get_extra_info(self, name: str) -> Any:
        return (self.address, 443) if name == "server_addr" and self.address else None


def observed(
    handler: Callable[[httpx2.Request], httpx2.Response],
    resolved: list[str],
) -> tuple[HttpTransport, list[str]]:
    calls: list[str] = []

    async def resolver(host: str) -> list[str]:
        calls.append(host)
        return resolved

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    instance = HttpTransport(
        client,
        limits=TransportLimits(max_attempts=3, min_interval_seconds=0),
        data_mode=DataMode.FIXTURE,
        clock=lambda: datetime(2026, 10, 25, tzinfo=UTC),
        resolver=resolver,
    )
    # An injected client can never be OBSERVED in production; force the mode so the
    # guard under test is the only difference from the production owned-client path.
    instance._mode = DataMode.OBSERVED
    return instance, calls


def ok_response(peer: str | None) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[], extensions={"network_stream": Stream(peer)})

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [["127.0.0.1"], ["10.0.0.5"], [PUBLIC_V4, "127.0.0.1"], []])
async def test_observed_host_resolving_to_a_non_public_address_is_refused_before_connect(
    resolved: list[str],
) -> None:
    reached: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        reached.append(request)
        return httpx2.Response(200, json=[])

    instance, calls = observed(handler, resolved)
    with pytest.raises(TransportError) as error:
        await instance.request(Source.POLYMARKET, "GET", "/markets", params={"limit": "1"})
    assert error.value.code == "unsafe_destination"
    assert reached == [], "no connection may be attempted"
    assert calls == ["gamma-api.polymarket.com"]
    assert instance.request_count == 0, "a refused destination consumes no request budget"
    assert instance.health[Source.POLYMARKET].last_code == "unsafe_destination"


@pytest.mark.asyncio
async def test_a_refused_destination_is_permanent_not_retried() -> None:
    instance, calls = observed(ok_response(PUBLIC_V4), ["127.0.0.1"])
    with pytest.raises(TransportError):
        await instance.request(Source.POLYMARKET, "GET", "/markets", params={"limit": "1"})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_public_resolution_and_public_peer_proceeds() -> None:
    instance, _ = observed(ok_response(PUBLIC_V4), [PUBLIC_V4, PUBLIC_V6])
    capture = await instance.request(Source.POLYMARKET, "GET", "/markets", params={"limit": "1"})
    assert capture.data_mode == DataMode.OBSERVED and capture.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", ["127.0.0.1", "192.168.0.7", None])
async def test_observed_response_from_a_non_public_or_unknown_peer_is_refused(
    peer: str | None,
) -> None:
    """DNS can change between the check and the connect; verify who actually answered."""
    instance, _ = observed(ok_response(peer), [PUBLIC_V4])
    with pytest.raises(TransportError) as error:
        await instance.request(Source.POLYMARKET, "GET", "/markets", params={"limit": "1"})
    assert error.value.code == "unsafe_destination"
    assert error.value.capture is None, "no body from an unverified peer is retained"


@pytest.mark.asyncio
async def test_fixture_transport_is_not_subject_to_the_destination_guard() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[])

    async def resolver(host: str) -> list[str]:
        raise AssertionError("fixture transports never resolve names")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    instance = HttpTransport(client, resolver=resolver)  # injected client => FIXTURE
    capture = await instance.request(Source.POLYMARKET, "GET", "/markets", params={"limit": "1"})
    assert capture.data_mode == DataMode.FIXTURE


@pytest.mark.asyncio
async def test_real_client_exposes_the_peer_address_the_guard_depends_on() -> None:
    """Loopback only, no external network: the production check must see a real peer IP.

    If the HTTP client stopped exposing ``server_addr`` the guard would fail closed on every
    request, so pin the assumption against a local server.
    """
    import asyncio

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx2.AsyncClient(trust_env=False) as client:
            async with client.stream("GET", f"http://127.0.0.1:{port}/") as response:
                stream = response.extensions["network_stream"]
                assert stream.get_extra_info("server_addr")[0] == "127.0.0.1"
                with pytest.raises(TransportError) as error:
                    HttpTransport._require_public_peer(Source.POLYMARKET, response)
                assert error.value.code == "unsafe_destination"
    finally:
        server.close()
        await server.wait_closed()
