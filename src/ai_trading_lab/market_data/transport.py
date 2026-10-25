"""Bounded public-data transport; external content never selects destinations."""

from __future__ import annotations

import asyncio
import copy
import http.cookiejar
import ipaddress
import math
import re
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import JsonValue

from ai_trading_lab.market_data.models import Capture, DataMode, Source, TransportError

Resolver = Callable[[str], Awaitable[list[str]]]


class _RefuseCookies(http.cookiejar.CookiePolicy):
    """Accept and send no cookie, ever: public reads carry no session state or credentials.

    Installing it replaces the cookie policy of the HTTP client handed to the transport (clients
    the transport builds itself are the production case; injected ones are fixtures).
    """

    netscape = True
    rfc2965 = False
    hide_cookie2 = False

    def set_ok(self, cookie: http.cookiejar.Cookie, request: object) -> bool:
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: object) -> bool:
        return False

    def domain_return_ok(self, domain: str, request: object) -> bool:
        return False

    def path_return_ok(self, path: str, request: object) -> bool:
        return False


def is_public_address(text: str) -> bool:
    """True only for a globally routable unicast address.

    OBSERVED evidence must come from the public internet. Loopback, private, link-local,
    carrier-grade NAT, documentation, reserved, multicast and malformed values are refused,
    including IPv4-mapped IPv6 spellings of them.
    """
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


async def _system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


_KRAKEN_PAIRS = r"[A-Z0-9]{5,12}(,[A-Z0-9]{5,12}){0,9}"
_BASE58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_TICKER = r"[A-Za-z0-9_-]{1,200}"


def validate_request(
    source: Source,
    method: Literal["GET", "POST"],
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, JsonValue] | None = None,
) -> str:
    """Return a built-in URL only after validating the complete read-only request."""

    def deny() -> None:
        raise TransportError(source, "request_not_allowed")

    if not isinstance(source, Source) or any(c in path for c in ("?", "#", "%", "\\", ":", "..")):
        deny()
    query = params or {}
    allowed: set[str] = set()
    origin = ""
    permitted = False
    if source == Source.DEXSCREENER:
        origin = "https://api.dexscreener.com"
        permitted = method == "GET" and (
            path == "/token-profiles/latest/v1"
            or re.fullmatch(r"/token-pairs/v1/solana/" + _BASE58, path) is not None
        )
    elif source == Source.SOLANA:
        origin = "https://api.mainnet.solana.com"
        permitted = method == "POST" and path == "/" and json_body is not None
        if json_body is not None:
            rpc_params = json_body.get("params")
            permitted = permitted and (
                set(json_body) <= {"jsonrpc", "id", "method", "params"}
                and json_body.get("jsonrpc") == "2.0"
                and (
                    (
                        isinstance(json_body.get("id"), int)
                        and not isinstance(json_body.get("id"), bool)
                    )
                    or json_body.get("id") == json_body.get("method")
                )
                and json_body.get("method") in ("getTokenSupply", "getTokenLargestAccounts")
                and isinstance(rpc_params, list)
                and 1 <= len(rpc_params) <= 2
                and isinstance(rpc_params[0], str)
                and re.fullmatch(_BASE58, rpc_params[0]) is not None
            )
            if isinstance(rpc_params, list) and len(rpc_params) == 2:
                options = rpc_params[1]
                permitted = permitted and isinstance(options, dict)
                if isinstance(options, dict):
                    permitted = (
                        permitted
                        and set(options) <= {"commitment"}
                        and options.get("commitment", "finalized")
                        in ("processed", "confirmed", "finalized")
                    )
    elif source == Source.POLYMARKET:
        permitted = method == "GET" and path in ("/markets", "/book")
        origin = (
            "https://gamma-api.polymarket.com"
            if path == "/markets"
            else "https://clob.polymarket.com"
        )
        allowed = (
            {"limit", "offset", "active", "closed", "id", "condition_ids"}
            if path == "/markets"
            else {"token_id"}
        )
        if path == "/book" and not re.fullmatch(r"[0-9]{1,100}", query.get("token_id", "")):
            deny()
    elif source == Source.KALSHI:
        origin = "https://external-api.kalshi.com/trade-api/v2"
        permitted = method == "GET" and (
            path == "/markets"
            or re.fullmatch(r"/markets/" + _TICKER + "/orderbook", path) is not None
        )
        allowed = {"limit", "cursor", "status"} if path == "/markets" else {"depth"}
    elif source == Source.KRAKEN:
        origin = "https://api.kraken.com"
        # Five public reads only. Nothing under /0/private is reachable, and no POST exists.
        kraken_params = {
            "/0/public/Time": set(),
            "/0/public/AssetPairs": {"pair"},
            "/0/public/Ticker": {"pair"},
            "/0/public/Depth": {"pair", "count"},
            "/0/public/OHLC": {"pair", "interval", "since"},
        }
        permitted = method == "GET" and path in kraken_params
        allowed = kraken_params.get(path, set())
        if any(not isinstance(value, str) for value in query.values()):
            deny()
        if path in kraken_params and path != "/0/public/Time" and "pair" not in query:
            deny()  # a pair is always required; AssetPairs without one returns the whole universe
        if "pair" in query and not re.fullmatch(_KRAKEN_PAIRS, query["pair"]):
            deny()
        if path == "/0/public/Depth" and "pair" in query and "," in query["pair"]:
            deny()  # Kraken's depth and candle endpoints are single-pair
        if path == "/0/public/OHLC" and "pair" in query and "," in query["pair"]:
            deny()
        if "count" in query and not (
            re.fullmatch(r"[0-9]{1,3}", query["count"]) and 1 <= int(query["count"]) <= 500
        ):
            deny()
        if "interval" in query and query["interval"] not in {
            "1",
            "5",
            "15",
            "30",
            "60",
            "240",
            "1440",
        }:
            deny()
        if "since" in query and not re.fullmatch(r"[0-9]{1,12}", query["since"]):
            deny()
    if not permitted or set(query) - allowed or (method != "POST" and json_body is not None):
        deny()
    for key, value in query.items():
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_,.=-]{1,1000}", value):
            deny()
        if key in {"limit", "offset", "depth"} and not re.fullmatch(r"[0-9]{1,6}", value):
            deny()
        if key in {"active", "closed"} and value not in {"true", "false"}:
            deny()
    return origin + path


@dataclass(frozen=True)
class TransportLimits:
    max_requests: int = 30
    timeout_seconds: float = 5
    total_seconds: float = 60
    max_response_bytes: int = 2_000_000
    max_attempts: int = 3
    concurrency: int = 2
    min_interval_seconds: float = 0.25
    backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        for value in (
            self.max_requests,
            self.max_response_bytes,
            self.max_attempts,
            self.concurrency,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Transport count limits must be positive integers")
        for seconds in (self.timeout_seconds, self.total_seconds):
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("Transport time limits must be positive and finite")
        for seconds in (self.min_interval_seconds, self.backoff_seconds):
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Transport delays must be nonnegative and finite")


@dataclass(frozen=True)
class SourceHealth:
    status: str = "unknown"
    last_code: str | None = None
    requests: int = 0
    rate_limited_until: float | None = None


class HttpTransport:
    def __init__(
        self,
        client: httpx2.AsyncClient | None = None,
        *,
        limits: TransportLimits | None = None,
        data_mode: DataMode | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        resolver: Resolver | None = None,
    ) -> None:
        mode = data_mode or (DataMode.OBSERVED if client is None else DataMode.FIXTURE)
        if client is not None and mode != DataMode.FIXTURE:
            raise ValueError("Injected HTTP clients require FIXTURE provenance, never OBSERVED")
        self._client = (
            client
            if client is not None
            else httpx2.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "AI-Trading-Lab/0.1 public-market-research"},
            )
        )
        self._owned = client is None
        # Real hosts set tracking cookies on the first response; refuse them all so the
        # credentials guard in request() cannot trip on a later call and no state is kept.
        self._client.cookies.jar.set_policy(_RefuseCookies())
        self._resolve: Resolver = resolver or _system_resolver
        self._limits = limits or TransportLimits()
        self._mode, self._clock, self._sleep, self._monotonic = mode, clock, sleep, monotonic
        self._deadline = monotonic() + self._limits.total_seconds
        self._semaphore = asyncio.Semaphore(self._limits.concurrency)
        self._source_locks = {source: asyncio.Lock() for source in Source}
        self._last_request: dict[Source, float] = {}
        self._health = dict.fromkeys(Source, SourceHealth())
        self._request_count = 0

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def health(self) -> Mapping[Source, SourceHealth]:
        return MappingProxyType(self._health.copy())

    async def __aenter__(self) -> HttpTransport:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    def _remaining(self, source: Source) -> float:
        remaining = self._deadline - self._monotonic()
        if remaining <= 0:
            raise TransportError(source, "time_budget_exhausted")
        return remaining

    async def _require_public_destination(self, source: Source, url: str) -> None:
        """Refuse to connect when the fixed host resolves anywhere but the public internet."""
        host = urlsplit(url).hostname or ""
        try:
            async with asyncio.timeout(min(self._limits.timeout_seconds, self._remaining(source))):
                addresses = await self._resolve(host)
        except (OSError, TimeoutError):
            return  # unresolvable: the request itself fails as an ordinary network error
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise TransportError(source, "unsafe_destination")

    @staticmethod
    def _require_public_peer(source: Source, response: httpx2.Response) -> None:
        """Verify who actually answered; DNS may differ between the check and the connect."""
        stream = response.extensions.get("network_stream")
        lookup = getattr(stream, "get_extra_info", None)
        server = lookup("server_addr") if callable(lookup) else None
        peer = server[0] if isinstance(server, tuple) and server else None
        if not isinstance(peer, str) or not is_public_address(peer):
            raise TransportError(source, "unsafe_destination")

    async def _pause(self, source: Source, delay: float) -> None:
        if delay >= self._remaining(source):
            raise TransportError(source, "time_budget_exhausted")
        if delay > 0:
            await self._sleep(delay)
        self._remaining(source)

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        params = copy.deepcopy(params)
        json_body = copy.deepcopy(json_body)
        url = validate_request(source, method, path, params=params, json_body=json_body)
        if (
            self._client.auth is not None
            or self._client.cookies
            or any(
                key.lower() in {"authorization", "proxy-authorization", "cookie", "x-api-key"}
                for key in self._client.headers
            )
            or self._client.params
        ):
            raise TransportError(source, "client_credentials_not_allowed")
        # Whole-call timeout includes waiting for concurrency and per-source rate slots.
        try:
            async with asyncio.timeout(self._remaining(source)):
                async with self._semaphore, self._source_locks[source]:
                    return await self._request(source, method, path, url, params, json_body)
        except TimeoutError:
            self._record(source, "time_budget_exhausted")
            raise TransportError(source, "time_budget_exhausted") from None
        except TransportError as error:
            self._record(source, error.code)
            raise

    def _record(self, source: Source, code: str | None) -> None:
        old = self._health[source]
        self._health[source] = SourceHealth(
            "healthy" if code is None else "degraded",
            code,
            old.requests,
            old.rate_limited_until,
        )

    async def _request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        url: str,
        params: dict[str, str] | None,
        json_body: dict[str, JsonValue] | None,
    ) -> Capture:
        for attempt in range(self._limits.max_attempts):
            blocked_until = self._health[source].rate_limited_until
            if blocked_until is not None:
                await self._pause(source, max(0, blocked_until - self._monotonic()))
            last = self._last_request.get(source)
            if last is not None:
                await self._pause(
                    source, max(0, last + self._limits.min_interval_seconds - self._monotonic())
                )
            if self._mode == DataMode.OBSERVED:
                await self._require_public_destination(source, url)
            if self._request_count >= self._limits.max_requests:
                raise TransportError(source, "request_budget_exhausted")
            timeout = min(self._limits.timeout_seconds, self._remaining(source))
            self._request_count += 1
            self._last_request[source] = self._monotonic()
            old = self._health[source]
            self._health[source] = SourceHealth(
                old.status, old.last_code, old.requests + 1, old.rate_limited_until
            )
            capture: Capture | None = None
            retry_after: str | None = None
            try:
                async with asyncio.timeout(timeout):
                    async with self._client.stream(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        timeout=timeout,
                        follow_redirects=False,
                        auth=None,
                    ) as response:
                        if self._mode == DataMode.OBSERVED:
                            self._require_public_peer(source, response)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65_536):
                            if len(raw) + len(chunk) > self._limits.max_response_bytes:
                                raise TransportError(source, "response_too_large")
                            raw.extend(chunk)
                        try:
                            body = raw.decode("utf-8", errors="strict")
                        except UnicodeDecodeError:
                            raise TransportError(source, "invalid_encoding") from None
                        capture = Capture(
                            source=source,
                            method=method,
                            path=path,
                            params=params or {},
                            request_body=json_body,
                            body=body,
                            status_code=response.status_code,
                            received_at_utc=self._clock(),
                            data_mode=self._mode,
                        )
                        retry_after = response.headers.get("Retry-After")
            except httpx2.DecodingError:
                raise TransportError(source, "invalid_content_encoding") from None
            except (httpx2.TimeoutException, TimeoutError):
                code = "timeout"
            except httpx2.TransportError:
                code = "network_error"
            else:
                if 200 <= capture.status_code < 300:
                    try:
                        capture.payload()
                    except (ValueError, RecursionError):
                        raise TransportError(source, "invalid_json", capture) from None
                    self._record(source, None)
                    return capture
                if capture.status_code == 429:
                    code = "rate_limited"
                elif 500 <= capture.status_code < 600:
                    code = "upstream_error"
                else:
                    raise TransportError(source, "http_status_rejected", capture)
            self._record(source, code)
            delay = self._limits.backoff_seconds * (2**attempt)
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry_after) - self._clock()).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        raise TransportError(source, "invalid_retry_after", capture) from None
                if not math.isfinite(delay) or delay < 0:
                    raise TransportError(source, "invalid_retry_after", capture)
            if code == "rate_limited":
                old = self._health[source]
                self._health[source] = SourceHealth(
                    old.status, old.last_code, old.requests, self._monotonic() + delay
                )
            if attempt + 1 >= self._limits.max_attempts:
                raise TransportError(source, code, capture)
            await self._pause(source, delay)
        raise AssertionError("positive attempts enforced")
