import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx2
import pytest

from ai_trading_lab.market_data.models import DataMode, Source, TransportError
from ai_trading_lab.market_data.transport import HttpTransport, TransportLimits, validate_request


class Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.value += seconds


def transport(
    handler: Callable[[httpx2.Request], httpx2.Response], clock: Clock, **limits: int | float
) -> tuple[HttpTransport, httpx2.AsyncClient]:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    settings = TransportLimits(**limits)  # type: ignore[arg-type]
    return HttpTransport(
        client,
        limits=settings,
        data_mode=DataMode.FIXTURE,
        clock=lambda: datetime(2026, 10, 18, tzinfo=UTC),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    ), client


@pytest.mark.parametrize(
    ("source", "method", "path", "params", "body"),
    [
        (Source.DEXSCREENER, "POST", "/token-profiles/latest/v1", None, None),
        (Source.DEXSCREENER, "GET", "https://evil.test/", None, None),
        (Source.DEXSCREENER, "GET", "//evil.test/", None, None),
        (Source.POLYMARKET, "GET", "/markets?evil=true", None, None),
        (Source.POLYMARKET, "GET", "/../markets", None, None),
        (Source.KALSHI, "GET", "/markets/%2e%2e/orderbook", None, None),
        (Source.KALSHI, "GET", "/portfolio/orders", None, None),
        (
            Source.SOLANA,
            "POST",
            "/",
            None,
            {"jsonrpc": "2.0", "method": "sendTransaction", "params": []},
        ),
        (
            Source.SOLANA,
            "POST",
            "/",
            None,
            {"jsonrpc": "2.0", "method": "getTokenSupply", "params": ["bad"]},
        ),
        (Source.POLYMARKET, "GET", "/book", {"token_id": "123&evil=true"}, None),
        (Source.POLYMARKET, "GET", "/markets", {"api_key": "secret"}, None),
        (Source.POLYMARKET, "GET", "/markets", None, {"method": "write"}),
    ],
)
@pytest.mark.asyncio
async def test_denied_before_network(
    source: Source,
    method: str,
    path: str,
    params: dict[str, str] | None,
    body: dict[str, object] | None,
) -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200, text="{}")

    instance, client = transport(handler, Clock())
    async with client:
        with pytest.raises(TransportError, match="request_not_allowed"):
            await instance.request(source, method, path, params=params, json_body=body)  # type: ignore[arg-type]
    assert calls == []
    assert instance.request_count == 0


@pytest.mark.asyncio
async def test_precision_capture_origin_and_injected_ownership() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text='{"price":0.1234567890123456789}')

    instance, client = transport(handler, Clock())
    async with instance:
        capture = await instance.request(
            Source.POLYMARKET, "GET", "/book", params={"token_id": "123"}
        )
    assert not client.is_closed
    await client.aclose()
    assert str(requests[0].url) == "https://clob.polymarket.com/book?token_id=123"
    assert capture.payload() == {"price": Decimal("0.1234567890123456789")}
    assert capture.data_mode == DataMode.FIXTURE
    assert capture.received_at_utc == datetime(2026, 10, 18, tzinfo=UTC)
    assert instance.health[Source.POLYMARKET].status == "healthy"


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.asyncio
async def test_retry_then_success_with_rate_status(status: int) -> None:
    clock = Clock()
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            status if count == 1 else 200, text="{}", headers={"Retry-After": "2"}
        )

    instance, client = transport(handler, clock)
    async with client:
        await instance.request(Source.KALSHI, "GET", "/markets")
    assert count == instance.request_count == 2
    assert clock.delays == [2]
    assert instance.health[Source.KALSHI].requests == 2


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("private malformed body", "invalid_json"),
        ('{"x":NaN}', "invalid_json"),
        ("{} ", "response_too_large"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_payload(text: str, code: str) -> None:
    instance, client = transport(
        lambda request: httpx2.Response(200, text=text),
        Clock(),
        max_response_bytes=2 if code == "response_too_large" else 100,
    )
    async with client:
        with pytest.raises(TransportError) as caught:
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert caught.value.code == code
    assert text not in str(caught.value)
    assert instance.request_count == 1
    assert instance.health[Source.KALSHI].status == "degraded"


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404])
@pytest.mark.asyncio
async def test_no_redirect_or_client_error_retry(status: int) -> None:
    instance, client = transport(
        lambda request: httpx2.Response(
            status, text="sensitive", headers={"Location": "https://evil.test"}
        ),
        Clock(),
    )
    async with client:
        with pytest.raises(TransportError) as caught:
            await instance.request(Source.POLYMARKET, "GET", "/markets")
    assert caught.value.code == "http_status_rejected"
    assert caught.value.capture is not None
    assert caught.value.capture.body == "sensitive"
    assert "sensitive" not in str(caught.value)
    assert instance.request_count == 1


@pytest.mark.asyncio
async def test_timeouts_retry_and_request_budget() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("sensitive", request=request)

    instance, client = transport(handler, Clock(), max_requests=2)
    async with client:
        with pytest.raises(TransportError, match="request_budget_exhausted"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert instance.request_count == 2


@pytest.mark.asyncio
async def test_attempt_exhaustion() -> None:
    instance, client = transport(lambda request: httpx2.Response(503, text="{}"), Clock())
    async with client:
        with pytest.raises(TransportError, match="upstream_error"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert instance.request_count == 3


@pytest.mark.asyncio
async def test_retry_after_exceeding_budget_does_not_retry_early() -> None:
    clock = Clock()
    instance, client = transport(
        lambda request: httpx2.Response(429, text="{}", headers={"Retry-After": "120"}), clock
    )
    async with client:
        with pytest.raises(TransportError, match="time_budget_exhausted"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert instance.request_count == 1
    assert clock.delays == []
    assert instance.health[Source.KALSHI].rate_limited_until == 120


@pytest.mark.asyncio
async def test_source_rate_spacing() -> None:
    clock = Clock()
    instance, client = transport(lambda request: httpx2.Response(200, text="{}"), clock)
    async with client:
        await instance.request(Source.KALSHI, "GET", "/markets")
        await instance.request(Source.KALSHI, "GET", "/markets")
    assert clock.delays == [0.25]


@pytest.mark.asyncio
async def test_expired_budget_does_not_make_request() -> None:
    clock = Clock()
    instance, client = transport(lambda request: httpx2.Response(200, text="{}"), clock)
    clock.value = 61
    async with client:
        with pytest.raises(TransportError, match="time_budget_exhausted"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert instance.request_count == 0


@pytest.mark.asyncio
async def test_owned_client_closed() -> None:
    instance = HttpTransport()
    async with instance:
        pass
    assert instance._client.is_closed


def test_allowlist_all_sources_preserves_native_case() -> None:
    mint = "So11111111111111111111111111111111111111112"
    assert validate_request(Source.DEXSCREENER, "GET", f"/token-pairs/v1/solana/{mint}").endswith(
        mint
    )
    assert validate_request(Source.DEXSCREENER, "GET", "/token-profiles/latest/v1").startswith(
        "https://api.dexscreener.com/"
    )
    assert validate_request(
        Source.SOLANA,
        "POST",
        "/",
        json_body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint, {"commitment": "finalized"}],
        },
    ).startswith("https://api.mainnet.solana.com")
    assert validate_request(Source.POLYMARKET, "GET", "/markets").startswith(
        "https://gamma-api.polymarket.com"
    )
    assert validate_request(Source.KALSHI, "GET", "/markets/My-Ticker/orderbook").endswith(
        "My-Ticker/orderbook"
    )


@pytest.mark.parametrize(
    "limits",
    [
        {"max_requests": 0},
        {"max_attempts": 0},
        {"concurrency": -1},
        {"timeout_seconds": float("inf")},
        {"total_seconds": 0},
        {"min_interval_seconds": -1},
    ],
)
def test_invalid_limits(limits: dict[str, int | float]) -> None:
    with pytest.raises(ValueError):
        TransportLimits(**limits)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": "placeholder"}, {"Cookie": "placeholder"}, {"X-API-Key": "placeholder"}],
)
@pytest.mark.asyncio
async def test_injected_credentials_rejected(headers: dict[str, str]) -> None:
    called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, text="{}")

    instance, client = transport(handler, Clock())
    client.headers.update(headers)
    async with client:
        with pytest.raises(TransportError, match="client_credentials_not_allowed"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert not called


@pytest.mark.parametrize("header", ["not a date", "NaN", "-1", "inf"])
@pytest.mark.asyncio
async def test_invalid_retry_after(header: str) -> None:
    instance, client = transport(
        lambda request: httpx2.Response(429, text="{}", headers={"Retry-After": header}), Clock()
    )
    async with client:
        with pytest.raises(TransportError, match="invalid_retry_after"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert instance.request_count == 1


@pytest.mark.asyncio
async def test_http_date_retry_after() -> None:
    clock = Clock()
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            429 if count == 1 else 200,
            text="{}",
            headers={"Retry-After": "Sun, 18 Oct 2026 00:00:03 GMT"},
        )

    instance, client = transport(handler, clock)
    async with client:
        await instance.request(Source.KALSHI, "GET", "/markets")
    assert clock.delays == [3]


@pytest.mark.parametrize("kind", ["timeout", "network_error"])
@pytest.mark.asyncio
async def test_transport_failure_sanitized(kind: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if kind == "timeout":
            raise httpx2.ReadTimeout("private data", request=request)
        raise httpx2.ConnectError("private data", request=request)

    instance, client = transport(handler, Clock())
    async with client:
        with pytest.raises(TransportError) as caught:
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert caught.value.code == kind
    assert caught.value.capture is None
    assert "private data" not in str(caught.value)
    assert instance.request_count == 3


@pytest.mark.asyncio
async def test_oversized_stream_closed_without_reading_tail() -> None:
    class Stream(httpx2.AsyncByteStream):
        closed = False
        tail_read = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"a" * 65_536
            self.tail_read = True
            yield b"b" * 65_536

        async def aclose(self) -> None:
            self.closed = True

    stream = Stream()
    instance, client = transport(
        lambda request: httpx2.Response(200, stream=stream), Clock(), max_response_bytes=10
    )
    async with client:
        with pytest.raises(TransportError, match="response_too_large"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert stream.closed
    assert not stream.tail_read


@pytest.mark.asyncio
async def test_invalid_encoding() -> None:
    instance, client = transport(lambda request: httpx2.Response(200, content=b"\xff"), Clock())
    async with client:
        with pytest.raises(TransportError, match="invalid_encoding"):
            await instance.request(Source.KALSHI, "GET", "/markets")


@pytest.mark.asyncio
async def test_next_call_respects_rate_limit_after_attempt_exhaustion() -> None:
    clock = Clock()
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(429 if count == 1 else 200, text="{}", headers={"Retry-After": "3"})

    instance, client = transport(handler, clock, max_attempts=1)
    async with client:
        with pytest.raises(TransportError, match="rate_limited"):
            await instance.request(Source.KALSHI, "GET", "/markets")
        await instance.request(Source.KALSHI, "GET", "/markets")
    assert clock.delays == [3]


@pytest.mark.asyncio
async def test_concurrency_bound_and_single_global_request_budget() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    active = maximum = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1
        return httpx2.Response(200, text="{}")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    instance = HttpTransport(client, limits=TransportLimits(concurrency=1, max_requests=1))
    async with client:
        first = asyncio.create_task(instance.request(Source.KALSHI, "GET", "/markets"))
        await entered.wait()
        second = asyncio.create_task(instance.request(Source.POLYMARKET, "GET", "/markets"))
        release.set()
        await first
        with pytest.raises(TransportError, match="request_budget_exhausted"):
            await second
    assert maximum == 1
    assert instance.request_count == 1


@pytest.mark.asyncio
async def test_total_deadline_cancels_stalled_stream() -> None:
    released = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Event().wait()
        finally:
            released.set()
        raise AssertionError("unreachable")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    instance = HttpTransport(client, limits=TransportLimits(total_seconds=0.01))
    async with client:
        with pytest.raises(TransportError, match="time_budget_exhausted"):
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert released.is_set()
    assert instance.request_count == 1


@pytest.mark.asyncio
async def test_real_solana_adapter_contract_through_transport() -> None:
    from ai_trading_lab.market_data.adapters.solana import SolanaAdapter

    mint = "So11111111111111111111111111111111111111112"
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        method = body["method"]
        requested.append(method)
        assert str(request.url) == "https://api.mainnet.solana.com/"
        assert body["params"] == [mint, {"commitment": "finalized"}]
        value = {"amount": "12345", "decimals": 9} if method == "getTokenSupply" else []
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"context": {"slot": 123}, "value": value},
            },
        )

    instance, client = transport(handler, Clock())
    async with client:
        batch = await SolanaAdapter(instance).collect([mint], limit=1)
    assert batch.failures == ()
    assert len(batch.captures) == len(batch.observations) == 2
    assert requested == ["getTokenSupply", "getTokenLargestAccounts"]
    assert instance.request_count == 2


@pytest.mark.parametrize("rpc_id", ["unbounded-or-unrelated", "sendTransaction", True])
def test_rpc_id_rejects_arbitrary_strings(rpc_id: str | bool) -> None:
    with pytest.raises(TransportError, match="request_not_allowed"):
        validate_request(
            Source.SOLANA,
            "POST",
            "/",
            json_body={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "getTokenSupply",
                "params": ["So11111111111111111111111111111111111111112"],
            },
        )


@pytest.mark.asyncio
async def test_malformed_compressed_response_sanitized_and_stream_closed() -> None:
    class CorruptStream(httpx2.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"private invalid gzip response"

        async def aclose(self) -> None:
            self.closed = True

    stream = CorruptStream()
    instance, client = transport(
        lambda request: httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream),
        Clock(),
    )
    async with client:
        with pytest.raises(TransportError) as caught:
            await instance.request(Source.KALSHI, "GET", "/markets")
    assert caught.value.code == "invalid_content_encoding"
    assert caught.value.capture is None
    assert caught.value.__cause__ is None
    assert str(caught.value) == "kalshi: invalid_content_encoding"
    assert instance.request_count == 1
    assert instance.health[Source.KALSHI].status == "degraded"
    assert instance.health[Source.KALSHI].last_code == "invalid_content_encoding"
    assert stream.closed


@pytest.mark.asyncio
async def test_solana_collection_records_decoding_failure_instead_of_raising() -> None:
    from ai_trading_lab.market_data.adapters.solana import SolanaAdapter

    class CorruptStream(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"private invalid gzip response"

    instance, client = transport(
        lambda request: httpx2.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=CorruptStream()
        ),
        Clock(),
    )
    async with client:
        batch = await SolanaAdapter(instance).collect(
            ["So11111111111111111111111111111111111111112"], limit=1
        )
    assert batch.captures == ()
    assert batch.observations == ()
    assert len(batch.failures) == 2
    assert all(failure.code == "invalid_content_encoding" for failure in batch.failures)
    assert "private invalid gzip response" not in batch.model_dump_json()
    assert instance.request_count == 2
    assert instance.health[Source.SOLANA].status == "degraded"
