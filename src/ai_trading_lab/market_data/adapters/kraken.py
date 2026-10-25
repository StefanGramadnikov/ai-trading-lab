"""Kraken public spot data: server time, pair rules and fees, ticker, order book and candles.

Read-only and keyless: five public GETs, no private endpoint, no order placement. Fees, tick
size and minimum order sizes come from Kraken's own ``AssetPairs`` response, never from memory.
Book level order is validated, never repaired, and a book is only ever paired with the server
time requested just before it, with that basis recorded in the observation's facts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import Field, JsonValue

from ai_trading_lab.market_data.models import (
    Batch,
    Capture,
    DataQuality,
    Failure,
    FrozenModel,
    Instrument,
    Observation,
    PriceLevel,
    Source,
    Transport,
    TransportError,
)

DEFAULT_PAIRS = ("XBTUSD", "ETHUSD", "SOLUSD")
INTERVALS = (1, 5, 15, 30, 60, 240, 1440)
_PAIR = re.compile(r"[A-Z0-9]{5,12}")
_PLAIN_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
MAX_UNIX = 4_102_444_800  # 2100-01-01: anything later is corrupt, and would overflow datetimes
MAX_PAIRS = 8  # keeps one collection within the 30-capture archive bound (2 + 3 per pair)
TIME_SKEW_SECONDS = 2  # a book level may not be stamped later than the server time labelling it
MAX_TIME_GAP_SECONDS = 5  # server time must be this close to the book it labels
_WSNAME = re.compile(r"([A-Z0-9]{2,10})/([A-Z0-9]{2,10})")
_TIME_BASIS = "kraken server time from /0/public/Time requested immediately before the book"


class Candle(FrozenModel):
    """A closed OHLC candle. Prices and volume are exact decimals in the pair's units."""

    open_time_utc: datetime
    close_time_utc: datetime
    interval_minutes: int = Field(ge=1)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal
    volume: Decimal
    trades: int = Field(ge=0)


def _decimal(value: object, *, positive: bool = True) -> Decimal:
    """Strict fixed-point string: finite, bounded magnitude and scale, never a float."""
    if not isinstance(value, str) or _PLAIN_DECIMAL.fullmatch(value) is None:
        raise ValueError("expected plain fixed-point string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid decimal") from None
    exponent = int(number.as_tuple().exponent)
    if (
        not number.is_finite()
        or number.adjusted() > 24
        or exponent < -24
        or (number <= 0 if positive else number < 0)
    ):
        raise ValueError("decimal out of range")
    return number


def _int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 10**12:
        raise ValueError("expected bounded integer")
    return value


def _unix(value: object) -> int:
    """A plausible Unix timestamp in seconds."""
    if type(value) is not int or not 1 <= value <= MAX_UNIX:
        raise ValueError("implausible unix timestamp")
    return value


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return cast(dict[str, object], value)


def _result(capture: Capture, path: str, params: dict[str, str]) -> dict[str, object]:
    """Validate a capture and return ``result``; a non-empty error list is a failure at HTTP 200."""
    if (
        capture.source != Source.KRAKEN
        or capture.method != "GET"
        or capture.path != path
        or capture.params != params
        or capture.request_body is not None
        or capture.status_code != 200
    ):
        raise ValueError("returned capture does not match successful request")
    payload = _object(capture.payload())
    errors = payload.get("error")
    if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
        raise ValueError("malformed error list")
    # Kraken prefixes errors with "E" and non-fatal warnings with "W"; only errors fail a response.
    errors = [e for e in errors if not e.startswith("W")]
    if errors:
        limited = any(
            e.startswith(("EAPI:Rate limit", "EGeneral:Temporary lockout")) for e in errors
        )
        raise TransportError(Source.KRAKEN, "rate_limited" if limited else "source_error", capture)
    return _object(payload.get("result"))


def _fee_tiers(value: object) -> list[list[JsonValue]]:
    if not isinstance(value, list):
        raise ValueError("fee schedule shape")
    tiers: list[list[JsonValue]] = []
    previous = Decimal(-1)
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("fee tier shape")
        volume = Decimal(str(row[0]))
        percent = Decimal(str(row[1]))
        if not (volume.is_finite() and percent.is_finite()) or volume <= previous:
            raise ValueError("fee tiers must have increasing volume")
        if not Decimal(0) <= percent <= Decimal(10):
            raise ValueError("fee percent out of range")
        previous = volume
        tiers.append([str(volume), str(percent)])
    return tiers


def _pair_rules(info: object) -> tuple[str, str, dict[str, JsonValue]]:
    data = _object(info)
    wsname = data.get("wsname")
    match = _WSNAME.fullmatch(wsname) if isinstance(wsname, str) else None
    if match is None:
        raise ValueError("wsname required")
    tick = _decimal(data.get("tick_size"))
    ordermin = _decimal(data.get("ordermin"))
    costmin = _decimal(data.get("costmin"), positive=False)
    lot_decimals = _int(data.get("lot_decimals"))
    pair_decimals = _int(data.get("pair_decimals"))
    base, quote = data.get("base"), data.get("quote")
    fee_currency = data.get("fee_volume_currency")
    if not all(isinstance(x, str) and x for x in (base, quote, fee_currency, data.get("altname"))):
        raise ValueError("pair identity incomplete")
    metadata: dict[str, JsonValue] = {
        "altname": cast(str, data["altname"]),
        "wsname": cast(str, wsname),
        "kraken_base": cast(str, base),
        "kraken_quote": cast(str, quote),
        "tick_size": str(tick),
        "ordermin": str(ordermin),
        "costmin": str(costmin),
        "lot_decimals": lot_decimals,
        "pair_decimals": pair_decimals,
        "fee_volume_currency": cast(str, fee_currency),
        "status": cast(str, data.get("status")),
    }
    # The anonymous API now returns empty fee arrays (observed 2026-10-25, also with info=fees).
    # An empty schedule is recorded as not published; fees then come from the broker's configured
    # fee model, never from a guess made here. A present schedule is validated and kept.
    taker, maker = _fee_tiers(data.get("fees")), _fee_tiers(data.get("fees_maker"))
    metadata["fees_published"] = bool(taker and maker)
    if taker and maker:
        metadata["fees_taker"] = cast(JsonValue, taker)
        metadata["fees_maker"] = cast(JsonValue, maker)
    elif taker or maker:
        raise ValueError("incomplete fee schedule")
    return match.group(1), match.group(2), metadata


def _levels(
    value: object, *, descending: bool, limit: int
) -> tuple[tuple[PriceLevel, ...], int | None]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("expected bounded level array")
    levels: list[PriceLevel] = []
    newest: int | None = None
    for row in value:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("level shape")
        levels.append(PriceLevel(price=_decimal(row[0]), quantity=_decimal(row[1])))
        stamp = _unix(row[2])
        newest = stamp if newest is None else max(newest, stamp)
    for first, second in zip(levels, levels[1:], strict=False):
        if (first.price <= second.price) if descending else (first.price >= second.price):
            raise ValueError("unordered_book")
    return tuple(levels), newest


def parse_candles(
    capture: Capture, *, server_time_utc: datetime, expected_key: str | None = None
) -> tuple[Candle, ...]:
    """Closed candles only, strictly increasing and internally consistent; malformed means error."""
    interval = int(capture.params["interval"])
    if interval not in INTERVALS:
        raise ValueError("unsupported interval")
    result = _result(capture, "/0/public/OHLC", dict(capture.params))
    keys = [key for key in result if key != "last"]
    if len(keys) != 1 or not isinstance(result[keys[0]], list):
        raise ValueError("exactly one pair of candles expected")
    if expected_key is not None and keys[0] != expected_key:
        raise ValueError("candles belong to a different pair")
    rows = cast(list[object], result[keys[0]])
    if len(rows) > 1000:  # Kraken returns up to 720 closed candles plus the forming one
        raise ValueError("candle window exceeds bound")
    candles: list[Candle] = []
    previous = -1
    for row in rows:
        if not isinstance(row, list) or len(row) != 8:
            raise ValueError("candle shape")
        opened = _unix(row[0])
        if opened <= previous:
            raise ValueError("candles must be strictly increasing")
        previous = opened
        values = [_decimal(row[index]) for index in range(1, 6)]
        volume = _decimal(row[6], positive=False)
        open_, high, low, close, vwap = values
        if not (low <= open_ <= high and low <= close <= high and low <= vwap <= high):
            raise ValueError("inconsistent candle")
        begin = datetime.fromtimestamp(opened, UTC)
        end = begin + timedelta(minutes=interval)
        if end > server_time_utc:
            continue  # still forming: never treated as evidence
        candles.append(
            Candle(
                open_time_utc=begin,
                close_time_utc=end,
                interval_minutes=interval,
                open=open_,
                high=high,
                low=low,
                close=close,
                vwap=vwap,
                volume=volume,
                trades=_int(row[7]),
            )
        )
    return tuple(candles)


def _candle_fact(candle: Candle) -> JsonValue:
    return {
        "open_time_utc": candle.open_time_utc.isoformat(),
        "close_time_utc": candle.close_time_utc.isoformat(),
        "interval_minutes": candle.interval_minutes,
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "vwap": str(candle.vwap),
        "volume": str(candle.volume),
        "trades": candle.trades,
    }


class KrakenAdapter:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def collect(
        self,
        pairs: Sequence[str] = DEFAULT_PAIRS,
        *,
        candle_interval_minutes: int = 15,
        book_depth: int = 25,
        candles_since: int | None = None,
    ) -> Batch:
        """Collect pair rules, one ticker call, then Time + book + candles for each pair in turn.

        Each book is labelled with the server time requested immediately before it, so event
        time and book belong to the same moment. A rate limit stops the whole collection.
        """
        if (
            not 1 <= len(pairs) <= MAX_PAIRS
            or len(set(pairs)) != len(pairs)
            or not all(isinstance(p, str) and _PAIR.fullmatch(p) for p in pairs)
            or type(candle_interval_minutes) is not int
            or candle_interval_minutes not in INTERVALS
            or type(book_depth) is not int
            or not 1 <= book_depth <= 100
            or (candles_since is not None and (type(candles_since) is not int or candles_since < 1))
        ):
            raise ValueError("collection bounds exceeded or malformed pair")
        captures: list[Capture] = []
        observations: list[Observation] = []
        failures: list[Failure] = []

        def fail(target: str, code: str) -> None:
            failures.append(
                Failure(
                    source=Source.KRAKEN,
                    target=target,
                    code=code,
                    message="Public market-data collection incomplete",
                )
            )

        def keep(exc: TransportError) -> None:
            if exc.capture is not None and exc.capture not in captures:
                captures.append(exc.capture)

        def batch() -> Batch:
            return Batch(
                captures=tuple(captures),
                observations=tuple(observations),
                failures=tuple(failures),
            )

        async def get(path: str, params: dict[str, str]) -> tuple[Capture, dict[str, object]]:
            capture = await self.transport.request(Source.KRAKEN, "GET", path, params=params)
            captures.append(capture)
            return capture, _result(capture, path, params)

        # 1. Pair rules, keyed by altname (the name we asked for), never by response order.
        requested = ",".join(pairs)
        try:
            pairs_capture, pairs_result = await get("/0/public/AssetPairs", {"pair": requested})
        except TransportError as exc:
            keep(exc)
            fail("/0/public/AssetPairs", exc.code)
            return batch()
        except (ValueError, TypeError):
            fail("/0/public/AssetPairs", "malformed_pairs")
            return batch()
        by_alt: dict[str, tuple[str, object]] = {}
        for key, info in pairs_result.items():
            if isinstance(info, dict) and isinstance(info.get("altname"), str):
                by_alt[cast(str, info["altname"])] = (key, info)
        rules: dict[str, tuple[str, str, str, dict[str, JsonValue]]] = {}
        for pair in pairs:
            entry = by_alt.get(pair)
            if entry is None or _object(entry[1]).get("status") != "online":
                fail(pair, "pair_unavailable")
                continue
            try:
                base, quote, metadata = _pair_rules(entry[1])
            except (ValueError, TypeError, InvalidOperation):
                fail(pair, "malformed_pair")
                continue
            rules[pair] = (entry[0], base, quote, metadata)
        if not rules:
            return batch()
        live = [pair for pair in pairs if pair in rules]

        # 2. Ticker for all live pairs in one request; its absence only degrades quality.
        tickers: dict[str, dict[str, JsonValue] | None] = dict.fromkeys(live)
        ticker_capture: Capture | None = None
        ticker_failed = False
        try:
            ticker_capture, ticker_result = await get("/0/public/Ticker", {"pair": ",".join(live)})
        except TransportError as exc:
            keep(exc)
            fail("/0/public/Ticker", exc.code)
            if exc.code == "rate_limited":
                return batch()
            ticker_failed = True
        except (ValueError, TypeError):
            fail("/0/public/Ticker", "malformed_ticker")
            ticker_failed = True
        if not ticker_failed:
            for pair in live:
                try:
                    raw = _object(ticker_result.get(rules[pair][0]))
                    last = _decimal(cast(list[object], raw["c"])[0])
                    tickers[pair] = cast(
                        dict[str, JsonValue],
                        {
                            "volume_24h": str(
                                _decimal(cast(list[object], raw["v"])[1], positive=False)
                            ),
                            "vwap_24h": str(
                                _decimal(cast(list[object], raw["p"])[1], positive=False)
                            ),
                            "trades_24h": _int(cast(list[object], raw["t"])[1]),
                            "low_24h": str(_decimal(cast(list[object], raw["l"])[1])),
                            "high_24h": str(_decimal(cast(list[object], raw["h"])[1])),
                            "open_today": str(_decimal(raw["o"])),
                            "last_trade_price": str(last),
                        },
                    )
                except (ValueError, TypeError, KeyError, IndexError, InvalidOperation):
                    fail(pair, "malformed_ticker")

        # 3. Per pair: its own server time, then its book, then its candles.
        for pair in live:
            key, base, quote, metadata = rules[pair]
            issues: list[str] = []
            try:
                time_capture, time_result = await get("/0/public/Time", {})
                unix_time = _unix(time_result.get("unixtime"))
                server_time = datetime.fromtimestamp(unix_time, UTC)
            except TransportError as exc:
                keep(exc)
                fail(pair, exc.code)
                if exc.code == "rate_limited":
                    break
                continue
            except (ValueError, TypeError, OverflowError, OSError):
                fail(pair, "malformed_time")
                continue
            depth_params = {"pair": pair, "count": str(book_depth)}
            try:
                depth_capture, depth_result = await get("/0/public/Depth", depth_params)
                raw_book = _object(depth_result.get(key))
                try:
                    bids, newest_bid = _levels(
                        raw_book.get("bids"), descending=True, limit=book_depth
                    )
                    asks, newest_ask = _levels(
                        raw_book.get("asks"), descending=False, limit=book_depth
                    )
                except ValueError as exc:
                    if str(exc) == "unordered_book":
                        fail(pair, "unordered_book")
                        continue
                    raise
                if bids and asks and bids[0].price > asks[0].price:
                    fail(pair, "crossed_book")
                    continue
            except TransportError as exc:
                keep(exc)
                fail(pair, exc.code)
                if exc.code == "rate_limited":
                    break
                continue
            except (ValueError, TypeError, KeyError, InvalidOperation):
                fail(pair, "malformed_book")
                continue
            stamps = [s for s in (newest_bid, newest_ask) if s is not None]
            newest = max(stamps) if stamps else None
            if newest is not None and newest > unix_time + TIME_SKEW_SECONDS:
                # A level stamped after the time labelling it would be look-ahead mislabelling.
                fail(pair, "book_newer_than_time")
                continue
            gap = (depth_capture.received_at_utc - server_time).total_seconds()
            if gap > MAX_TIME_GAP_SECONDS:
                issues.append("time_basis_stale")
            if not bids and not asks:
                issues.append("empty_book")
            elif not bids or not asks:
                issues.append("one_sided_book")
            candle_fact: JsonValue = None
            candle_capture: Capture | None = None
            candle_params = {"pair": pair, "interval": str(candle_interval_minutes)}
            if candles_since is not None:
                candle_params["since"] = str(candles_since)
            try:
                candle_capture, _ = await get("/0/public/OHLC", candle_params)
                closed = parse_candles(
                    candle_capture, server_time_utc=server_time, expected_key=key
                )
                if closed:
                    candle_fact = _candle_fact(closed[-1])
                else:
                    issues.append("no_closed_candle")
            except TransportError as exc:
                keep(exc)
                fail(pair, exc.code)
                issues.append("candles_unavailable")
                if exc.code == "rate_limited":
                    observations.append(
                        _observation(
                            pair,
                            rules[pair],
                            depth_capture,
                            server_time,
                            bids,
                            asks,
                            newest,
                            issues,
                            tickers[pair],
                            ticker_capture,
                            time_capture,
                            pairs_capture,
                            None,
                            None,
                            book_depth,
                        )
                    )
                    break
            except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError, OSError):
                fail(pair, "malformed_candles")
                issues.append("candles_unavailable")
            observations.append(
                _observation(
                    pair,
                    rules[pair],
                    depth_capture,
                    server_time,
                    bids,
                    asks,
                    newest,
                    issues,
                    tickers[pair],
                    ticker_capture,
                    time_capture,
                    pairs_capture,
                    candle_fact,
                    candle_capture,
                    book_depth,
                )
            )
        return batch()


def _observation(
    pair: str,
    rule: tuple[str, str, str, dict[str, JsonValue]],
    depth_capture: Capture,
    server_time: datetime,
    bids: tuple[PriceLevel, ...],
    asks: tuple[PriceLevel, ...],
    newest: int | None,
    issues: list[str],
    ticker: dict[str, JsonValue] | None,
    ticker_capture: Capture | None,
    time_capture: Capture,
    pairs_capture: Capture,
    candle_fact: JsonValue,
    candle_capture: Capture | None,
    book_depth: int,
) -> Observation:
    key, base, quote, metadata = rule
    notes = list(issues)
    if ticker is None:
        notes.append("ticker_unavailable")
    facts: dict[str, JsonValue] = {
        "quantity_unit": base,
        "price_unit": quote,
        "event_time_basis": _TIME_BASIS,
        "newest_book_level_unix": newest,
        "book_depth_requested": book_depth,
        "time_capture_id": str(time_capture.capture_id),
        "pairs_capture_id": str(pairs_capture.capture_id),
        "last_closed_candle": candle_fact,
    }
    if ticker_capture is not None and ticker is not None:
        facts["ticker_capture_id"] = str(ticker_capture.capture_id)
        facts.update(ticker)
    if candle_capture is not None:
        facts["candles_capture_id"] = str(candle_capture.capture_id)
    price = Decimal(cast(str, ticker["last_trade_price"])) if ticker else None
    return Observation(
        capture_id=depth_capture.capture_id,
        instrument=Instrument(
            source=Source.KRAKEN,
            venue="kraken",
            native_id=key,
            kind="spot",
            base_asset=base,
            quote_currency=quote,
            title=f"{base}/{quote}",
            metadata=metadata,
        ),
        event_time_utc=server_time,
        received_at_utc=depth_capture.received_at_utc,
        quality=DataQuality.PARTIAL if notes else DataQuality.VALID,
        issues=tuple(notes),
        price=price,
        price_currency=quote if price is not None else None,
        bids=bids,
        asks=asks,
        facts=facts,
    ).assessed()
