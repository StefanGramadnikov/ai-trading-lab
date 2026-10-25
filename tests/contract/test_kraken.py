"""Synthetic Kraken public-API contract evidence (shapes follow Kraken's documented REST responses).

Real captured responses are checked separately in test_kraken_real_sample.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, cast

import pytest
from pydantic import JsonValue

from ai_trading_lab.market_data.adapters.kraken import (
    DEFAULT_PAIRS,
    KrakenAdapter,
    parse_candles,
)
from ai_trading_lab.market_data.models import (
    Capture,
    DataMode,
    DataQuality,
    Source,
    TransportError,
)
from ai_trading_lab.market_data.transport import validate_request

SERVER = 1_767_225_600  # 2026-01-01T00:00:00Z
RECEIVED = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
KEYS = {"XBTUSD": "XXBTZUSD", "ETHUSD": "XETHZUSD", "SOLUSD": "SOLUSD"}
BASE = {"XBTUSD": ("XXBT", "XBT"), "ETHUSD": ("XETH", "ETH"), "SOLUSD": ("SOL", "SOL")}


def pair_info(alt: str) -> dict[str, Any]:
    base, ws_base = BASE[alt]
    return {
        "altname": alt,
        "wsname": f"{ws_base}/USD",
        "aclass_base": "currency",
        "base": base,
        "aclass_quote": "currency",
        "quote": "ZUSD",
        "lot": "unit",
        "cost_decimals": 5,
        "pair_decimals": 1,
        "lot_decimals": 8,
        "lot_multiplier": 1,
        "fees": [[0, 0.4], [10000, 0.35]],
        "fees_maker": [[0, 0.25], [10000, 0.2]],
        "fee_volume_currency": "ZUSD",
        "ordermin": "0.0001",
        "costmin": "0.5",
        "tick_size": "0.1",
        "status": "online",
    }


def ticker(price: str = "67000.0") -> dict[str, Any]:
    return {
        "a": ["67000.1", "1", "1.000"],
        "b": ["66999.9", "2", "2.000"],
        "c": [price, "0.01"],
        "v": ["100.1", "1200.5"],
        "p": ["66900.1", "66500.2"],
        "t": [1000, 15000],
        "l": ["66000.0", "65000.0"],
        "h": ["67500.0", "68000.0"],
        "o": "66800.0",
    }


def book() -> dict[str, Any]:
    return {
        "asks": [["67000.1", "1.500", SERVER], ["67000.5", "2.000", SERVER - 5]],
        "bids": [["66999.9", "2.000", SERVER], ["66999.5", "1.000", SERVER - 3]],
    }


def candles() -> list[list[Any]]:
    rows = []
    for index, offset in enumerate((-2700, -1800, -900, 0)):
        price = 67000 + index
        rows.append(
            [
                SERVER + offset,
                f"{price}.0",
                f"{price + 10}.0",
                f"{price - 10}.0",
                f"{price + 5}.0",
                f"{price + 2}.0",
                "12.5",
                120 + index,
            ]
        )
    return rows


Responder = Callable[[str, dict[str, str]], object]


class Synthetic:
    """Answers each public request from a per-path function; records every call."""

    def __init__(self, overrides: dict[str, Responder] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.overrides = overrides or {}

    def default(self, path: str, params: dict[str, str]) -> object:
        pairs = params.get("pair", "").split(",")
        if path == "/0/public/Time":
            return {"error": [], "result": {"unixtime": SERVER, "rfc1123": "x"}}
        if path == "/0/public/AssetPairs":
            return {"error": [], "result": {KEYS[p]: pair_info(p) for p in pairs}}
        if path == "/0/public/Ticker":
            return {"error": [], "result": {KEYS[p]: ticker() for p in pairs}}
        if path == "/0/public/Depth":
            return {"error": [], "result": {KEYS[pairs[0]]: book()}}
        if path == "/0/public/OHLC":
            return {"error": [], "result": {KEYS[pairs[0]]: candles(), "last": SERVER - 900}}
        raise AssertionError(path)

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        validate_request(source, method, path, params=params, json_body=json_body)
        query = params or {}
        self.calls.append((path, query))
        reply = self.overrides.get(path, self.default)(path, query)
        if isinstance(reply, TransportError):
            raise reply
        return Capture(
            source=source,
            method=method,
            path=path,
            params=query,
            body=json.dumps(reply),
            data_mode=DataMode.FIXTURE,
            received_at_utc=RECEIVED,
        )


async def collect(transport: Synthetic, pairs: tuple[str, ...] = ("XBTUSD", "ETHUSD")) -> Any:
    return await KrakenAdapter(transport).collect(pairs)


@pytest.mark.asyncio
async def test_collects_valid_spot_observations_with_source_fees_and_server_time() -> None:
    transport = Synthetic()
    batch = await collect(transport, DEFAULT_PAIRS)
    assert not batch.failures
    assert [o.instrument.native_id for o in batch.observations] == [
        "XXBTZUSD",
        "XETHZUSD",
        "SOLUSD",
    ]
    first = batch.observations[0]
    instrument = first.instrument
    assert instrument.source == Source.KRAKEN and instrument.venue == "kraken"
    assert instrument.kind == "spot"
    assert (instrument.base_asset, instrument.quote_currency) == ("XBT", "USD")
    assert instrument.title == "XBT/USD"
    meta = instrument.metadata
    assert meta["altname"] == "XBTUSD" and meta["tick_size"] == "0.1"
    assert meta["lot_decimals"] == 8 and meta["ordermin"] == "0.0001"
    assert meta["fees_published"] is True
    assert meta["fees_taker"] == [["0", "0.4"], ["10000", "0.35"]]
    assert meta["fees_maker"] == [["0", "0.25"], ["10000", "0.2"]]
    assert first.quality == DataQuality.VALID and not first.issues
    assert first.price == Decimal("67000.0") and first.price_currency == "USD"
    assert first.event_time_utc == datetime(2026, 1, 1, tzinfo=UTC)
    assert first.received_at_utc == RECEIVED
    assert [(lv.price, lv.quantity) for lv in first.bids] == [
        (Decimal("66999.9"), Decimal("2.000")),
        (Decimal("66999.5"), Decimal("1.000")),
    ]
    assert [lv.price for lv in first.asks] == [Decimal("67000.1"), Decimal("67000.5")]
    assert not any(lv.derived for lv in (*first.bids, *first.asks))
    facts = first.facts
    assert facts["event_time_basis"].startswith("kraken server time")
    assert facts["volume_24h"] == "1200.5" and facts["vwap_24h"] == "66500.2"
    assert facts["quantity_unit"] == "XBT" and facts["price_unit"] == "USD"


@pytest.mark.asyncio
async def test_request_plan_is_bounded_read_only_and_ordered() -> None:
    transport = Synthetic()
    await collect(transport, ("XBTUSD", "ETHUSD"))
    paths = [path for path, _ in transport.calls]
    assert paths == [
        "/0/public/AssetPairs",
        "/0/public/Ticker",
        "/0/public/Time",
        "/0/public/Depth",
        "/0/public/OHLC",
        "/0/public/Time",
        "/0/public/Depth",
        "/0/public/OHLC",
    ]
    assert transport.calls[0][1] == {"pair": "XBTUSD,ETHUSD"}
    assert transport.calls[1][1] == {"pair": "XBTUSD,ETHUSD"}
    assert transport.calls[3][1] == {"pair": "XBTUSD", "count": "25"}
    assert transport.calls[4][1] == {"pair": "XBTUSD", "interval": "15"}


@pytest.mark.asyncio
async def test_last_closed_candle_excludes_the_in_progress_candle() -> None:
    batch = await collect(Synthetic(), ("XBTUSD",))
    candle: Any = batch.observations[0].facts["last_closed_candle"]
    assert candle["open_time_utc"] == "2025-12-31T23:45:00+00:00"
    assert candle["close"] == "67007.0" and candle["interval_minutes"] == 15
    assert candle["volume"] == "12.5" and candle["trades"] == 122


def test_parse_candles_returns_only_closed_strictly_ordered_decimals() -> None:
    capture = Capture(
        source=Source.KRAKEN,
        method="GET",
        path="/0/public/OHLC",
        params={"pair": "XBTUSD", "interval": "15"},
        body=json.dumps({"error": [], "result": {"XXBTZUSD": candles(), "last": SERVER - 900}}),
        data_mode=DataMode.FIXTURE,
        received_at_utc=RECEIVED,
    )
    parsed = parse_candles(capture, server_time_utc=datetime(2026, 1, 1, tzinfo=UTC))
    assert [c.open_time_utc.timestamp() for c in parsed] == [
        SERVER - 2700,
        SERVER - 1800,
        SERVER - 900,
    ]
    assert all(isinstance(c.close, Decimal) for c in parsed)
    assert all(c.high >= c.low >= 0 for c in parsed)
    assert parsed[-1].close_time_utc == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.reverse(),  # not increasing
        lambda rows: rows.append(rows[0]),  # duplicate/older candle
        lambda rows: rows[0].__setitem__(2, "1.0"),  # high below low
        lambda rows: rows[0].__setitem__(1, "-5"),  # negative price
        lambda rows: rows[0].__setitem__(6, "NaN"),  # non-finite volume
        lambda rows: rows[0].pop(),  # short row
    ],
)
def test_malformed_candles_are_rejected_not_repaired(
    mutate: Callable[[list[list[Any]]], None],
) -> None:
    rows = candles()
    mutate(rows)
    capture = Capture(
        source=Source.KRAKEN,
        method="GET",
        path="/0/public/OHLC",
        params={"pair": "XBTUSD", "interval": "15"},
        body=json.dumps({"error": [], "result": {"XXBTZUSD": rows, "last": 1}}),
        data_mode=DataMode.FIXTURE,
        received_at_utc=RECEIVED,
    )
    with pytest.raises(ValueError):
        parse_candles(capture, server_time_utc=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_unordered_or_crossed_book_is_a_failure_never_sorted() -> None:
    def bad_book(_: str, params: dict[str, str]) -> object:
        data = book()
        data["asks"].reverse()  # worst first: the venue order is not guessed or repaired
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    batch = await collect(Synthetic({"/0/public/Depth": bad_book}), ("XBTUSD",))
    assert not batch.observations
    assert [f.code for f in batch.failures] == ["unordered_book"]

    def crossed(_: str, params: dict[str, str]) -> object:
        data = book()
        data["bids"][0][0] = "67001.0"
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    batch = await collect(Synthetic({"/0/public/Depth": crossed}), ("XBTUSD",))
    assert [f.code for f in batch.failures] == ["crossed_book"]


@pytest.mark.asyncio
async def test_empty_book_is_partial_not_valid() -> None:
    def empty(_: str, params: dict[str, str]) -> object:
        return {"error": [], "result": {KEYS[params["pair"]]: {"asks": [], "bids": []}}}

    batch = await collect(Synthetic({"/0/public/Depth": empty}), ("XBTUSD",))
    observation = batch.observations[0]
    assert observation.quality == DataQuality.PARTIAL and "empty_book" in observation.issues


@pytest.mark.asyncio
async def test_missing_ticker_degrades_to_partial_without_inventing_a_price() -> None:
    def no_ticker(_: str, __: dict[str, str]) -> object:
        return TransportError(Source.KRAKEN, "timeout")

    batch = await collect(Synthetic({"/0/public/Ticker": no_ticker}), ("XBTUSD",))
    observation = batch.observations[0]
    assert observation.price is None and observation.quality == DataQuality.PARTIAL
    assert "ticker_unavailable" in observation.issues
    assert [f.code for f in batch.failures] == ["timeout"]


@pytest.mark.asyncio
async def test_missing_server_time_means_no_observations() -> None:
    def down(_: str, __: dict[str, str]) -> object:
        return TransportError(Source.KRAKEN, "network_error")

    batch = await collect(Synthetic({"/0/public/Time": down}), ("XBTUSD",))
    assert not batch.observations and [f.code for f in batch.failures] == ["network_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("errors", "code"),
    [
        (["EQuery:Unknown asset pair"], "source_error"),
        (["EAPI:Rate limit exceeded"], "rate_limited"),
        (["EGeneral:Temporary lockout"], "rate_limited"),
        (["EService:Unavailable"], "source_error"),
    ],
)
async def test_non_empty_error_list_is_a_failure_even_with_http_200(
    errors: list[str], code: str
) -> None:
    def failing(_: str, __: dict[str, str]) -> object:
        return {"error": errors, "result": {}}

    batch = await collect(Synthetic({"/0/public/AssetPairs": failing}), ("XBTUSD",))
    assert not batch.observations and [f.code for f in batch.failures] == [code]


@pytest.mark.asyncio
async def test_pair_identity_comes_from_altname_never_from_response_order() -> None:
    def swapped(_: str, params: dict[str, str]) -> object:
        # Kraken answers with keys in its own order; identity must follow altname.
        return {
            "error": [],
            "result": {"XETHZUSD": pair_info("ETHUSD"), "XXBTZUSD": pair_info("XBTUSD")},
        }

    batch = await collect(Synthetic({"/0/public/AssetPairs": swapped}), ("XBTUSD", "ETHUSD"))
    assert [o.instrument.metadata["altname"] for o in batch.observations] == ["XBTUSD", "ETHUSD"]
    assert [o.instrument.native_id for o in batch.observations] == ["XXBTZUSD", "XETHZUSD"]


@pytest.mark.asyncio
async def test_unknown_or_offline_pair_is_skipped_with_a_failure() -> None:
    def partial(_: str, params: dict[str, str]) -> object:
        info = pair_info("XBTUSD")
        info["status"] = "cancel_only"
        return {"error": [], "result": {"XXBTZUSD": info}}

    batch = await collect(Synthetic({"/0/public/AssetPairs": partial}), ("XBTUSD", "ETHUSD"))
    assert not batch.observations
    assert sorted(f.target for f in batch.failures) == ["ETHUSD", "XBTUSD"]
    assert {f.code for f in batch.failures} == {"pair_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"fees": [[0, 0.4], [0, 0.3]]},  # non-increasing volume tiers
        {"fees": [[0, 0.4]], "fees_maker": []},  # only half a schedule
        {"tick_size": "0"},
        {"ordermin": "-1"},
        {"lot_decimals": "8"},  # wrong type
    ],
)
async def test_malformed_pair_metadata_is_rejected(change: dict[str, Any]) -> None:
    def bad(_: str, params: dict[str, str]) -> object:
        return {"error": [], "result": {"XXBTZUSD": pair_info("XBTUSD") | change}}

    batch = await collect(Synthetic({"/0/public/AssetPairs": bad}), ("XBTUSD",))
    assert not batch.observations and batch.failures


@pytest.mark.asyncio
async def test_unpublished_fees_are_recorded_not_invented_and_do_not_degrade_the_quote() -> None:
    """Observed against the live API on 2026-10-25: fee arrays come back empty."""

    def unpublished(_: str, params: dict[str, str]) -> object:
        info = pair_info("XBTUSD") | {"fees": [], "fees_maker": []}
        return {"error": [], "result": {"XXBTZUSD": info}}

    batch = await collect(Synthetic({"/0/public/AssetPairs": unpublished}), ("XBTUSD",))
    observation = batch.observations[0]
    assert observation.quality == DataQuality.VALID and not batch.failures
    meta = observation.instrument.metadata
    assert meta["fees_published"] is False
    assert "fees_taker" not in meta and "fees_maker" not in meta


@pytest.mark.asyncio
@pytest.mark.parametrize("price", ["0", "-1", "NaN", "1e999999", "abc"])
async def test_invalid_last_trade_price_is_not_accepted(price: str) -> None:
    def bad(_: str, params: dict[str, str]) -> object:
        return {"error": [], "result": {"XXBTZUSD": ticker(price)}}

    batch = await collect(Synthetic({"/0/public/Ticker": bad}), ("XBTUSD",))
    observation = batch.observations[0]
    assert observation.price is None and "ticker_unavailable" in observation.issues


@pytest.mark.asyncio
async def test_pairs_are_bounded_unique_and_well_formed() -> None:
    adapter = KrakenAdapter(Synthetic())
    for bad in (
        ("xbtusd",),
        ("XBT/USD",),
        ("",),
        ("XBTUSD", "XBTUSD"),
        tuple(f"AAA{n}USD" for n in range(9)),
        (),
    ):
        with pytest.raises(ValueError):
            await adapter.collect(bad)
    with pytest.raises(ValueError):
        await adapter.collect(("XBTUSD",), candle_interval_minutes=7)
    with pytest.raises(ValueError):
        await adapter.collect(("XBTUSD",), book_depth=0)


def test_transport_allows_only_the_five_public_reads() -> None:
    ok = [
        ("/0/public/Time", {}),
        ("/0/public/AssetPairs", {"pair": "XBTUSD,ETHUSD"}),
        ("/0/public/Ticker", {"pair": "XBTUSD"}),
        ("/0/public/Depth", {"pair": "XBTUSD", "count": "25"}),
        ("/0/public/OHLC", {"pair": "XBTUSD", "interval": "15", "since": "1767225600"}),
    ]
    for path, params in ok:
        assert validate_request(Source.KRAKEN, "GET", path, params=params) == (
            "https://api.kraken.com" + path
        )
    denied: list[tuple[Literal["GET", "POST"], str, dict[str, str]]] = [
        ("POST", "/0/public/Time", {}),
        ("GET", "/0/private/Balance", {}),
        ("POST", "/0/private/AddOrder", {}),
        ("GET", "/0/public/Trades", {"pair": "XBTUSD"}),
        ("GET", "/0/public/Depth", {"pair": "XBTUSD", "count": "25", "evil": "1"}),
        ("GET", "/0/public/Depth", {"pair": "xbtusd"}),
        ("GET", "/0/public/Depth", {"pair": "XBTUSD", "count": "abc"}),
        ("GET", "/0/public/Depth", {"pair": "XBTUSD", "count": "9999"}),
        ("GET", "/0/public/OHLC", {"pair": "XBTUSD", "interval": "7"}),
        ("GET", "/0/public/OHLC", {"pair": "XBTUSD", "since": "-1"}),
        ("GET", "/0/public/Depth", {"pair": ",".join(["XBTUSD"] * 11)}),
        ("GET", "/0/public/Time", {"pair": "XBTUSD"}),
        ("GET", "/0/public/AssetPairs", {}),
        ("GET", "/0/public/Ticker", {}),
        ("GET", "/0/public/Depth", {"count": "25"}),
        ("GET", "/0/public/OHLC", {"interval": "15"}),
        ("GET", "/0/public/AssetPairs", {"pair": "XBTUSD", "info": "fees"}),
        ("GET", "/0/public/Ticker", {"pair": "XBTUSD", "info": "fees"}),
        ("GET", "https://evil.test/0/public/Time", {}),
        ("GET", "/0/public/../private/Balance", {}),
    ]
    from ai_trading_lab.market_data.transport import validate_request as check

    for method, path, params in denied:
        with pytest.raises(TransportError) as error:
            check(Source.KRAKEN, method, path, params=params)
        assert error.value.code == "request_not_allowed", (method, path, params)


@pytest.mark.asyncio
async def test_archive_replays_to_the_same_observations_without_a_network() -> None:
    from ai_trading_lab.market_data.collector import collect as collect_source
    from ai_trading_lab.market_data.collector import replay
    from ai_trading_lab.market_data.replay import CaptureArchive

    live = await collect_source(
        Source.KRAKEN, Synthetic(), mint_addresses=("XBTUSD", "ETHUSD"), limit=5, max_pages=1
    )
    archive = CaptureArchive(
        source=Source.KRAKEN,
        mint_addresses=("XBTUSD", "ETHUSD"),
        limit=5,
        max_pages=1,
        captures=live.captures,
    )
    again = await replay(CaptureArchive.model_validate_json(archive.model_dump_json()))
    assert not again.failures
    assert [(o.instrument.native_id, o.price, o.bids, o.asks) for o in again.observations] == [
        (o.instrument.native_id, o.price, o.bids, o.asks) for o in live.observations
    ]
    assert {c.data_mode for c in again.captures} == {DataMode.REPLAY}


@pytest.mark.asyncio
async def test_collector_defaults_to_the_three_approved_pairs_and_bounds_the_list() -> None:
    from ai_trading_lab.market_data.collector import collect as collect_source

    batch = await collect_source(Source.KRAKEN, Synthetic(), limit=5, max_pages=1)
    assert [o.instrument.metadata["altname"] for o in batch.observations] == list(DEFAULT_PAIRS)
    with pytest.raises(ValueError):
        await collect_source(
            Source.KRAKEN,
            Synthetic(),
            mint_addresses=tuple(f"AAA{n}USD" for n in range(9)),
            limit=10,
            max_pages=1,
        )
    # An explicit pair list is not limited by --limit; the default list ignores it too.
    batch = await collect_source(Source.KRAKEN, Synthetic(), limit=1, max_pages=1)
    assert len(batch.observations) == 3


def test_cli_accepts_repeatable_pairs_for_kraken() -> None:
    from ai_trading_lab.market_data import cli

    args = cli.parser().parse_args(
        ["collect", "--source", "kraken", "--pair", "XBTUSD", "--pair", "ETHUSD", "--limit", "3"]
    )
    assert args.source == Source.KRAKEN and args.pair == ["XBTUSD", "ETHUSD"] and args.mint == []


def test_a_full_real_window_of_721_rows_is_accepted_but_unbounded_windows_are_not() -> None:
    """Observed 2026-10-25: Kraken returns 720 closed candles plus the one still forming."""

    def capture(count: int) -> Capture:
        rows = [
            [SERVER - 900 * (count - index), "10.0", "11.0", "9.0", "10.5", "10.2", "1.5", 3]
            for index in range(count)
        ]
        return Capture(
            source=Source.KRAKEN,
            method="GET",
            path="/0/public/OHLC",
            params={"pair": "XBTUSD", "interval": "15"},
            body=json.dumps({"error": [], "result": {"XXBTZUSD": rows, "last": 1}}),
            data_mode=DataMode.FIXTURE,
            received_at_utc=RECEIVED,
        )

    server = datetime(2026, 1, 1, tzinfo=UTC)
    assert len(parse_candles(capture(721), server_time_utc=server)) == 721
    with pytest.raises(ValueError, match="bound"):
        parse_candles(capture(1001), server_time_utc=server)


def _wrap(result: object, error: object = None) -> object:
    return {"error": [] if error is None else error, "result": result}


MALFORMED: list[tuple[str, Callable[[str, dict[str, str]], object]]] = [
    ("/0/public/Time", lambda p, q: _wrap({"unixtime": "x"})),
    ("/0/public/Time", lambda p, q: _wrap({"unixtime": True})),
    ("/0/public/Time", lambda p, q: _wrap({"unixtime": 0})),
    ("/0/public/Time", lambda p, q: _wrap({})),
    ("/0/public/Time", lambda p, q: _wrap([], error="boom")),
    ("/0/public/Time", lambda p, q: {"result": {"unixtime": SERVER}}),
    ("/0/public/Time", lambda p, q: [1, 2]),
    ("/0/public/AssetPairs", lambda p, q: _wrap([])),
    ("/0/public/AssetPairs", lambda p, q: _wrap({"XXBTZUSD": "not an object"})),
    (
        "/0/public/AssetPairs",
        lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"wsname": "XBTUSD"}}),
    ),
    (
        "/0/public/AssetPairs",
        lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"tick_size": "abc"}}),
    ),
    (
        "/0/public/AssetPairs",
        lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"ordermin": 1}}),
    ),
    ("/0/public/AssetPairs", lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"base": ""}})),
    ("/0/public/AssetPairs", lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"fees": "x"}})),
    (
        "/0/public/AssetPairs",
        lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"fees": [[0]]}}),
    ),
    (
        "/0/public/AssetPairs",
        lambda p, q: _wrap({"XXBTZUSD": pair_info("XBTUSD") | {"fees": [[0, 99]]}}),
    ),
    ("/0/public/Ticker", lambda p, q: _wrap([])),
    ("/0/public/Ticker", lambda p, q: _wrap({})),
    ("/0/public/Ticker", lambda p, q: _wrap({"XXBTZUSD": {"c": "x"}})),
    ("/0/public/Ticker", lambda p, q: _wrap({"XXBTZUSD": ticker() | {"v": ["1", "NaN"]}})),
    ("/0/public/Ticker", lambda p, q: _wrap({"XXBTZUSD": ticker() | {"t": [1, -5]}})),
    ("/0/public/Depth", lambda p, q: _wrap([])),
    ("/0/public/Depth", lambda p, q: _wrap({})),
    ("/0/public/Depth", lambda p, q: _wrap({"XXBTZUSD": {"asks": "x", "bids": []}})),
    ("/0/public/Depth", lambda p, q: _wrap({"XXBTZUSD": {"asks": [["1", "1"]], "bids": []}})),
    ("/0/public/Depth", lambda p, q: _wrap({"XXBTZUSD": {"asks": [["-1", "1", 5]], "bids": []}})),
    ("/0/public/Depth", lambda p, q: _wrap({"XXBTZUSD": {"asks": [["1", "1", "5"]], "bids": []}})),
    (
        "/0/public/Depth",
        lambda p, q: _wrap({"XXBTZUSD": {"asks": [["1", "1", 5]] * 30, "bids": []}}),
    ),
    ("/0/public/OHLC", lambda p, q: _wrap([])),
    ("/0/public/OHLC", lambda p, q: _wrap({"XXBTZUSD": "x", "last": 1})),
    ("/0/public/OHLC", lambda p, q: _wrap({"XXBTZUSD": [], "XETHZUSD": [], "last": 1})),
    ("/0/public/OHLC", lambda p, q: _wrap({"last": 1})),
    ("/0/public/OHLC", lambda p, q: _wrap({"XXBTZUSD": ["x"], "last": 1})),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "bad"), MALFORMED)
async def test_malformed_venue_data_never_raises_and_never_yields_a_clean_observation(
    path: str, bad: Callable[[str, dict[str, str]], object]
) -> None:
    batch = await collect(Synthetic({path: bad}), ("XBTUSD",))
    assert batch.failures, "malformed data must be reported"
    for observation in batch.observations:
        # Anything that survives is explicitly degraded, never silently clean.
        assert observation.quality != DataQuality.VALID and observation.issues
    if path in ("/0/public/Time", "/0/public/AssetPairs"):
        assert not batch.observations


@pytest.mark.asyncio
async def test_a_capture_that_does_not_match_its_request_is_rejected() -> None:
    class Wrong(Synthetic):
        async def request(
            self, source: Source, method: Literal["GET", "POST"], path: str, **kwargs: Any
        ) -> Capture:
            capture = await super().request(source, method, path, **kwargs)
            return (
                capture.model_copy(update={"status_code": 503})
                if path == "/0/public/Time"
                else capture
            )

    batch = await KrakenAdapter(Wrong()).collect(("XBTUSD",))
    assert not batch.observations and [f.code for f in batch.failures] == ["malformed_time"]


# ---- review round: coherence of event time, strict numbers, one-sided books, rate limits ----


@pytest.mark.asyncio
async def test_each_book_is_paired_with_its_own_adjacent_server_time() -> None:
    """The claim 'requested immediately before the book' is now structurally true."""
    transport = Synthetic()
    batch = await collect(transport, ("XBTUSD", "ETHUSD"))
    paths = [path for path, _ in transport.calls]
    for index, path in enumerate(paths):
        if path == "/0/public/Depth":
            assert paths[index - 1] == "/0/public/Time"
    ids = [o.facts["time_capture_id"] for o in batch.observations]
    assert len(set(ids)) == 2


@pytest.mark.asyncio
async def test_a_book_newer_than_its_server_time_is_rejected_as_lookahead() -> None:
    def future_levels(_: str, params: dict[str, str]) -> object:
        data = book()
        data["asks"][0][2] = SERVER + 30  # level stamped after the time it would be labelled with
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    batch = await collect(Synthetic({"/0/public/Depth": future_levels}), ("XBTUSD",))
    assert not batch.observations
    assert [f.code for f in batch.failures] == ["book_newer_than_time"]


@pytest.mark.asyncio
async def test_a_server_time_that_lags_the_book_degrades_the_observation() -> None:
    def lagging_time(_: str, __: dict[str, str]) -> object:
        return _wrap({"unixtime": SERVER - 30, "rfc1123": "x"})

    def older_book(_: str, params: dict[str, str]) -> object:
        data = book()
        for side in ("asks", "bids"):
            for level in data[side]:
                level[2] = SERVER - 40  # consistent with the lagging time, so only the gap shows
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    batch = await collect(
        Synthetic({"/0/public/Time": lagging_time, "/0/public/Depth": older_book}), ("XBTUSD",)
    )
    observation = batch.observations[0]
    assert observation.quality != DataQuality.VALID and "time_basis_stale" in observation.issues


@pytest.mark.asyncio
async def test_a_huge_candle_timestamp_is_a_failure_not_a_crash() -> None:
    def huge(_: str, params: dict[str, str]) -> object:
        rows = candles()
        rows[0][0] = 253_402_300_799  # year 9999: datetime arithmetic overflows
        return _wrap({KEYS[params["pair"]]: rows, "last": 1})

    batch = await collect(Synthetic({"/0/public/OHLC": huge}), ("XBTUSD",))
    assert [f.code for f in batch.failures] == ["malformed_candles"]
    assert "candles_unavailable" in batch.observations[0].issues


@pytest.mark.asyncio
async def test_candles_for_a_different_pair_are_rejected() -> None:
    def wrong_pair(_: str, params: dict[str, str]) -> object:
        return _wrap({"XETHZUSD": candles(), "last": 1})

    batch = await collect(Synthetic({"/0/public/OHLC": wrong_pair}), ("XBTUSD",))
    assert [f.code for f in batch.failures] == ["malformed_candles"]


@pytest.mark.asyncio
@pytest.mark.parametrize("price", ["1e5", "+1", " 1 ", "1_0", "0x10", "1,5", ".5", "5."])
async def test_decimal_strings_must_be_plain_fixed_point(price: str) -> None:
    def bad(_: str, params: dict[str, str]) -> object:
        return {"error": [], "result": {"XXBTZUSD": ticker(price)}}

    batch = await collect(Synthetic({"/0/public/Ticker": bad}), ("XBTUSD",))
    assert batch.observations[0].price is None


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["asks", "bids"])
async def test_a_one_sided_book_is_partial_and_names_the_missing_side(side: str) -> None:
    def one_sided(_: str, params: dict[str, str]) -> object:
        data = book()
        data[side] = []
        return {"error": [], "result": {KEYS[params["pair"]]: data}}

    batch = await collect(Synthetic({"/0/public/Depth": one_sided}), ("XBTUSD",))
    observation = batch.observations[0]
    assert observation.quality == DataQuality.PARTIAL and "one_sided_book" in observation.issues


@pytest.mark.asyncio
async def test_an_empty_book_records_no_invented_level_time() -> None:
    def empty(_: str, params: dict[str, str]) -> object:
        return {"error": [], "result": {KEYS[params["pair"]]: {"asks": [], "bids": []}}}

    batch = await collect(Synthetic({"/0/public/Depth": empty}), ("XBTUSD",))
    assert batch.observations[0].facts["newest_book_level_unix"] is None


@pytest.mark.asyncio
async def test_a_rate_limit_stops_the_collection_instead_of_hammering_the_venue() -> None:
    def limited(_: str, __: dict[str, str]) -> object:
        return {"error": ["EAPI:Rate limit exceeded"], "result": {}}

    transport = Synthetic({"/0/public/Depth": limited})
    batch = await collect(transport, DEFAULT_PAIRS)
    depth_calls = [path for path, _ in transport.calls if path == "/0/public/Depth"]
    assert len(depth_calls) == 1, "no further pair may be requested after a rate limit"
    assert not batch.observations
    assert "rate_limited" in [f.code for f in batch.failures]


@pytest.mark.asyncio
async def test_warning_only_error_lists_are_not_failures() -> None:
    def warned(path: str, params: dict[str, str]) -> object:
        reply = cast(dict[str, Any], Synthetic().default(path, params))
        reply["error"] = ["WGeneral:Deprecated field"]
        return reply

    batch = await collect(Synthetic({"/0/public/Ticker": warned}), ("XBTUSD",))
    assert not batch.failures and batch.observations[0].quality == DataQuality.VALID


@pytest.mark.asyncio
async def test_cli_rejects_pairs_for_other_sources() -> None:
    from ai_trading_lab.market_data import cli

    args = cli.parser().parse_args(["collect", "--source", "solana", "--pair", "XBTUSD"])
    with pytest.raises(ValueError, match="pair"):
        await cli.run(args)
