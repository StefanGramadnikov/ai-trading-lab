"""Bounded public Gamma metadata and CLOB snapshots; text remains untrusted data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
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
    parse_json,
)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty string")
    return value


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("expected exact number")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("nonfinite number")
    return result


def _timestamp(value: object) -> datetime:
    milliseconds = _decimal(value)
    # Bound before scaling: finite hostile exponents must not reach arithmetic.
    if not Decimal("-62135596800000") <= milliseconds <= Decimal("253402300799999.999"):
        raise ValueError("timestamp outside representable UTC date range")
    if milliseconds.is_zero():
        return datetime(1970, 1, 1, tzinfo=UTC)
    sign, digits, exponent = milliseconds.as_tuple()
    scale = int(exponent)
    if scale < -3:
        raise ValueError("timestamp exceeds microsecond precision")
    # Integer coefficient arithmetic preserves exact microseconds under any
    # ambient Decimal precision, without float conversion or Decimal scaling.
    coefficient = int("".join(str(digit) for digit in digits))
    microseconds = coefficient * 10 ** (scale + 3)
    if sign:
        microseconds = -microseconds
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=microseconds)


def _array(value: object) -> list[object]:
    if isinstance(value, str):
        value = parse_json(value)
    if not isinstance(value, list):
        raise ValueError("expected array")
    return cast(list[object], value)


def _levels(value: object) -> tuple[PriceLevel, ...]:
    levels = []
    for entry in _array(value):
        row = _object(entry)
        price, quantity = _decimal(row.get("price")), _decimal(row.get("size"))
        if not 0 <= price <= 1 or quantity <= 0:
            raise ValueError("invalid book level")
        levels.append(PriceLevel(price=price, quantity=quantity))
    return tuple(levels)


def _validate_capture(capture: Capture, path: str, params: dict[str, str]) -> None:
    if (
        capture.source != Source.POLYMARKET
        or capture.method != "GET"
        or capture.path != path
        or capture.params != params
        or capture.request_body is not None
        or capture.status_code != 200
    ):
        raise ValueError("returned capture does not match successful request")


class PolymarketAdapter:
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
        seen_markets: set[str] = set()
        seen_tokens: set[str] = set()

        def fail(target: str, code: str) -> None:
            failures.append(
                Failure(
                    source=Source.POLYMARKET,
                    target=target,
                    code=code,
                    message="Public market-data collection incomplete",
                )
            )

        for page in range(max_pages):
            params = {
                "active": "true",
                "closed": "false",
                "limit": str(limit),
                "offset": str(page * limit),
            }
            try:
                capture = await self.transport.request(
                    Source.POLYMARKET,
                    "GET",
                    "/markets",
                    params=params,
                )
                captures.append(capture)
                _validate_capture(capture, "/markets", params)
                markets = _array(capture.payload())
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
                    condition = _text(market.get("conditionId"))
                    _text(market.get("id"))
                    if condition in seen_markets:
                        fail(condition, "repeated_market")
                        continue
                    seen_markets.add(condition)
                    outcomes = [_text(x) for x in _array(market.get("outcomes"))]
                    tokens = [_text(x) for x in _array(market.get("clobTokenIds"))]
                    if (
                        len(tokens) != len(outcomes)
                        or len(tokens) < 2
                        or len(set(tokens)) != len(tokens)
                        or len(set(outcomes)) != len(outcomes)
                        or any(not x.isascii() or not x.isdigit() for x in tokens)
                    ):
                        raise ValueError("misaligned outcome IDs")
                    metadata = cast(
                        dict[str, JsonValue], json.loads(json.dumps(market, default=str))
                    )
                except (ValueError, TypeError):
                    fail("/markets", "malformed_market")
                    continue
                for token, outcome in zip(tokens, outcomes, strict=True):
                    if len(seen_tokens) >= limit:
                        return Batch(
                            captures=tuple(captures),
                            observations=tuple(observations),
                            failures=tuple(failures),
                        )
                    if token in seen_tokens:
                        fail(token, "repeated_token")
                        continue
                    seen_tokens.add(token)
                    try:
                        book_capture = await self.transport.request(
                            Source.POLYMARKET, "GET", "/book", params={"token_id": token}
                        )
                        captures.append(book_capture)
                        _validate_capture(book_capture, "/book", {"token_id": token})
                        book = _object(book_capture.payload())
                        if book.get("asset_id") != token or book.get("market") != condition:
                            fail(token, "book_identity_mismatch")
                            continue
                        bids, asks = _levels(book.get("bids")), _levels(book.get("asks"))
                        event_time = None
                        if book.get("timestamp") is not None:
                            event_time = _timestamp(book.get("timestamp"))
                        issues = () if bids or asks else ("empty_book",)
                        observations.append(
                            Observation(
                                capture_id=book_capture.capture_id,
                                instrument=Instrument(
                                    source=Source.POLYMARKET,
                                    venue="polymarket",
                                    native_id=token,
                                    kind="prediction",
                                    market_id=condition,
                                    outcome=outcome,
                                    title=cast(str | None, market.get("question")),
                                    metadata=metadata,
                                ),
                                received_at_utc=book_capture.received_at_utc,
                                event_time_utc=event_time,
                                quality=DataQuality.PARTIAL if issues else DataQuality.VALID,
                                issues=issues,
                                bids=bids,
                                asks=asks,
                                price_currency="collateral_units_per_contract",
                                facts={
                                    "metadata_capture_id": str(capture.capture_id),
                                    "quantity_unit": "contracts",
                                    "price_unit": "collateral_units_per_contract",
                                    "collateral_currency": "unspecified_by_book",
                                },
                            ).assessed()
                        )
                    except TransportError as exc:
                        if exc.capture is not None:
                            captures.append(exc.capture)
                        fail(token, exc.code)
                    except (ValueError, TypeError, DecimalException, OverflowError):
                        fail(token, "malformed_book")
            if len(observations) >= limit:
                break
            if page == max_pages - 1:
                fail("/markets", "page_limit_reached")
        return Batch(
            captures=tuple(captures), observations=tuple(observations), failures=tuple(failures)
        )
