"""Public binary books with explicitly derived complementary asks."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import cast

from pydantic import JsonValue

from ai_trading_lab.market_data.models import (
    Batch,
    Capture,
    DataQuality,
    Failure,
    Instrument,
    Observation,
    PriceLevel,
    Source,
    Transport,
    TransportError,
)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return cast(dict[str, object], value)


def _levels(value: object) -> tuple[PriceLevel, ...]:
    if not isinstance(value, list):
        raise ValueError("expected level array")
    result = []
    for row in value:
        if not isinstance(row, list) or len(row) != 2 or not all(isinstance(x, str) for x in row):
            raise ValueError("expected fixed-point strings")
        price, quantity = Decimal(row[0]), Decimal(row[1])
        if (
            not price.is_finite()
            or not quantity.is_finite()
            or not 0 <= price <= 1
            or quantity <= 0
            or int(price.as_tuple().exponent) < -1000
        ):
            raise ValueError("invalid level")
        result.append(PriceLevel(price=price, quantity=quantity))
    return tuple(result)


def _complement(price: Decimal) -> Decimal:
    # Precision follows validated decimal scale, independent of ambient context.
    with localcontext() as context:
        context.prec = max(28, len(price.as_tuple().digits), -int(price.as_tuple().exponent) + 1)
        return Decimal(1) - price


def _validate_capture(capture: Capture, path: str, params: dict[str, str]) -> None:
    if (
        capture.source != Source.KALSHI
        or capture.method != "GET"
        or capture.path != path
        or capture.params != params
        or capture.request_body is not None
        or capture.status_code != 200
    ):
        raise ValueError("returned capture does not match successful request")


class KalshiAdapter:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def collect(self, *, limit: int = 5, max_pages: int = 2) -> Batch:
        if (
            type(limit) is not int
            or type(max_pages) is not int
            or not 1 <= limit <= 10
            or not 1 <= max_pages <= 3
        ):
            raise ValueError("collection bounds exceeded")
        captures: list[Capture] = []
        observations: list[Observation] = []
        failures: list[Failure] = []
        cursor = ""
        seen_cursors: set[str] = set()
        seen_markets: set[str] = set()

        def fail(target: str, code: str) -> None:
            failures.append(
                Failure(
                    source=Source.KALSHI,
                    target=target,
                    code=code,
                    message="Public market-data collection incomplete",
                )
            )

        for page in range(max_pages):
            params = {"status": "open", "limit": str(limit)}
            if cursor:
                params["cursor"] = cursor
            try:
                capture = await self.transport.request(
                    Source.KALSHI, "GET", "/markets", params=params
                )
                captures.append(capture)
                _validate_capture(capture, "/markets", params)
                payload = _object(capture.payload())
                markets = payload.get("markets")
                next_cursor = payload.get("cursor", "")
                if not isinstance(markets, list) or not isinstance(next_cursor, str):
                    raise ValueError("invalid page")
            except TransportError as exc:
                if exc.capture is not None:
                    captures.append(exc.capture)
                fail("/markets", exc.code)
                break
            except (ValueError, TypeError):
                fail("/markets", "malformed_metadata")
                break
            if not markets:
                fail("/markets", "empty_page")
                break
            for raw_market in markets[:limit]:
                if len(observations) >= limit:
                    break
                try:
                    market = _object(raw_market)
                    ticker = market.get("ticker")
                    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", ticker):
                        raise ValueError("invalid ticker")
                    if ticker in seen_markets:
                        fail(ticker, "repeated_market")
                        continue
                    seen_markets.add(ticker)
                    if market.get("market_type") != "binary":
                        fail(ticker, "unsupported_market_type")
                        continue
                    metadata = cast(
                        dict[str, JsonValue], json.loads(json.dumps(market, default=str))
                    )
                    book_capture = await self.transport.request(
                        Source.KALSHI,
                        "GET",
                        f"/markets/{ticker}/orderbook",
                        params={"depth": "100"},
                    )
                    captures.append(book_capture)
                    _validate_capture(
                        book_capture, f"/markets/{ticker}/orderbook", {"depth": "100"}
                    )
                    book = _object(_object(book_capture.payload()).get("orderbook_fp"))
                    yes, no = _levels(book.get("yes_dollars")), _levels(book.get("no_dollars"))
                    for outcome, bids, opposite in (("YES", yes, no), ("NO", no, yes)):
                        if len(observations) >= limit:
                            break
                        asks = tuple(
                            PriceLevel(
                                price=_complement(level.price),
                                quantity=level.quantity,
                                derived=True,
                            )
                            for level in opposite
                        )
                        issues = ("empty_book",) if not bids and not asks else ()
                        observations.append(
                            Observation(
                                capture_id=book_capture.capture_id,
                                instrument=Instrument(
                                    source=Source.KALSHI,
                                    venue="kalshi",
                                    native_id=f"{ticker}:{outcome}",
                                    kind="prediction",
                                    market_id=ticker,
                                    outcome=outcome,
                                    quote_currency="USD",
                                    title=cast(str | None, market.get("title")),
                                    metadata=metadata,
                                ),
                                received_at_utc=book_capture.received_at_utc,
                                quality=DataQuality.PARTIAL,
                                issues=issues,
                                bids=bids,
                                asks=asks,
                                price_currency="USD_per_contract",
                                facts={
                                    "metadata_capture_id": str(capture.capture_id),
                                    "quantity_unit": "contracts",
                                    "price_unit": "USD_per_contract",
                                    "ask_derivation": "1 USD minus opposite outcome bid",
                                    "book_identity_basis": "requested market ticker path",
                                    "depth_limit": 100,
                                },
                            ).assessed()
                        )
                except TransportError as exc:
                    if exc.capture is not None:
                        captures.append(exc.capture)
                    fail(
                        str(raw_market.get("ticker", "unknown"))
                        if isinstance(raw_market, dict)
                        else "/markets",
                        exc.code,
                    )
                except (ValueError, TypeError, InvalidOperation):
                    fail("/markets", "malformed_market_or_book")
            if len(observations) >= limit or not next_cursor:
                break
            if next_cursor in seen_cursors:
                fail("/markets", "repeated_cursor")
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if page == max_pages - 1:
                fail("/markets", "page_limit_reached")
        return Batch(
            captures=tuple(captures), observations=tuple(observations), failures=tuple(failures)
        )
