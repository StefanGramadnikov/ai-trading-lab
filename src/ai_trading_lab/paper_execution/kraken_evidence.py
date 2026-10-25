"""Raw-capture evidence for Kraken orders: the quote must re-derive from the originals.

A live-observed paper portfolio only fills an OBSERVED quote when the order carries the three
captures it came from (pair rules, server time, book) and the quote derived from them equals the
quote submitted. A hand-built or altered quote therefore cannot become executable evidence.
Archive consistency is not proof of provider authenticity; transport honesty is enforced upstream.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid5

from pydantic import Field, JsonValue, model_validator

from ai_trading_lab.market_data.adapters.kraken import (
    MAX_TIME_GAP_SECONDS,
    TIME_SKEW_SECONDS,
    _levels,
    _object,
    _pair_rules,
    _result,
    _unix,
)
from ai_trading_lab.market_data.models import (
    Capture,
    DataQuality,
    FrozenModel,
    Source,
    TransportError,
)
from ai_trading_lab.paper_execution.models import ExecutionQuote, PaperInstrument


class KrakenEntry(FrozenModel):
    """The originals behind one book: pair rules, server time and depth, for one pair."""

    pairs_capture: Capture
    time_capture: Capture
    book_capture: Capture
    pair: str = Field(pattern=r"^[A-Z0-9]{5,12}$")
    assumption_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def qualified(self) -> KrakenEntry:
        self.derive_quote()
        return self

    @property
    def quote(self) -> ExecutionQuote:
        return self.derive_quote()

    @property
    def rules(self) -> dict[str, JsonValue]:
        """The venue's stable pair rules (tick size, minimum order and cost, decimals)."""
        result = _result(
            self.pairs_capture, "/0/public/AssetPairs", dict(self.pairs_capture.params)
        )
        info = next(
            v for v in result.values() if isinstance(v, dict) and v.get("altname") == self.pair
        )
        return _pair_rules(info)[2]

    def derive_quote(self) -> ExecutionQuote:
        captures = (self.pairs_capture, self.time_capture, self.book_capture)
        if len({c.data_mode for c in captures}) != 1:
            raise ValueError("kraken_evidence_mixed_data_mode")
        if any(c.source != Source.KRAKEN for c in captures):
            raise ValueError("kraken_evidence_source")
        if self.time_capture.received_at_utc > self.book_capture.received_at_utc:
            raise ValueError("kraken_evidence_time_after_book")
        try:
            return self._derive()
        except TransportError as exc:
            raise ValueError(f"kraken_evidence_source_error_{exc.code}") from None

    def _derive(self) -> ExecutionQuote:
        pairs = self.pairs_capture
        requested = pairs.params.get("pair", "").split(",")
        if self.pair not in requested:
            raise ValueError("kraken_evidence_pair_not_requested")
        result = _result(pairs, "/0/public/AssetPairs", dict(pairs.params))
        key, info = next(
            (
                (k, v)
                for k, v in result.items()
                if isinstance(v, dict) and v.get("altname") == self.pair
            ),
            (None, None),
        )
        if key is None or _object(info).get("status") != "online":
            raise ValueError("kraken_evidence_pair_unavailable")
        base, quote_currency, rules = _pair_rules(info)
        unix_time = _unix(
            _result(self.time_capture, "/0/public/Time", dict(self.time_capture.params)).get(
                "unixtime"
            )
        )
        server_time = datetime.fromtimestamp(unix_time, UTC)
        book = self.book_capture
        count = book.params.get("count", "")
        if (
            book.params.get("pair") != self.pair
            or not count.isdigit()
            or not 1 <= int(count) <= 100
        ):
            raise ValueError("kraken_evidence_book_request")
        raw_book = _object(_object(_result(book, "/0/public/Depth", dict(book.params))).get(key))
        bids, newest_bid = _levels(raw_book.get("bids"), descending=True, limit=int(count))
        asks, newest_ask = _levels(raw_book.get("asks"), descending=False, limit=int(count))
        if bids and asks and bids[0].price > asks[0].price:
            raise ValueError("kraken_evidence_crossed_book")
        stamps = [s for s in (newest_bid, newest_ask) if s is not None]
        if stamps and max(stamps) > unix_time + TIME_SKEW_SECONDS:
            raise ValueError("kraken_evidence_book_newer_than_time")
        gap = (book.received_at_utc - server_time).total_seconds()
        if gap < -TIME_SKEW_SECONDS:
            raise ValueError("kraken_evidence_time_in_future")
        instrument = PaperInstrument(
            source=Source.KRAKEN,
            venue="kraken",
            native_id=key,
            kind="spot",
            base_unit=base,
            cash_unit=quote_currency,
            tick_size=Decimal(cast(str, rules["tick_size"])),
            quantity_step=Decimal(1).scaleb(-cast(int, rules["lot_decimals"])),
            cash_step=Decimal(1).scaleb(-cast(int, rules["cost_decimals"])),
        )
        return ExecutionQuote(
            snapshot_id=uuid5(book.capture_id, "kraken-execution-quote"),
            instrument=instrument,
            event_time_utc=server_time,
            received_at_utc=book.received_at_utc,
            quality=DataQuality.VALID if gap <= MAX_TIME_GAP_SECONDS else DataQuality.PARTIAL,
            data_mode=book.data_mode,
            bids=bids,
            asks=asks,
        )
