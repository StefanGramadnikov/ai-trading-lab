"""Bounded, raw-capture-derived offline quotes; no observed execution interface.

Only the Polymarket parser currently supplies native identity, explicit depth and
source timestamps. Its collateral denomination remains unspecified: the generic
unit below deliberately cannot fund a USD portfolio. Archive consistency is not
proof of provider authenticity or a calibrated execution model.
"""

from __future__ import annotations

from typing import Literal
from uuid import uuid5

from pydantic import Field, model_validator

from ai_trading_lab.market_data.adapters.polymarket import (
    _array,
    _levels,
    _object,
    _text,
    _timestamp,
    _validate_capture,
)
from ai_trading_lab.market_data.models import (
    Capture,
    DataMode,
    DataQuality,
    FrozenModel,
    Instrument,
    Observation,
    Source,
    TransportError,
)
from ai_trading_lab.market_data.replay import CaptureArchive
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.models import ExecutionQuote, PaperInstrument

COLLATERAL_UNIT = "polymarket:unspecified_collateral"


class ReplayEntry(FrozenModel):
    """Original captures plus explicit nonoperational execution quanta/version."""

    metadata_capture: Capture
    book_capture: Capture
    instrument: PaperInstrument
    assumption_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def qualified(self) -> ReplayEntry:
        self.derive_quote()
        return self

    @property
    def quote(self) -> ExecutionQuote:
        return self.derive_quote()

    @property
    def resolution_terms_hash(self) -> str:
        for raw in _array(self.metadata_capture.payload()):
            row = _object(raw)
            if row.get(
                "conditionId"
            ) == self.instrument.market_id and self.instrument.native_id in _array(
                row.get("clobTokenIds")
            ):
                return content_hash(_text(row.get("description")))
        raise ValueError("replay_resolution_terms_missing")

    def derive_quote(self) -> ExecutionQuote:
        metadata, book = self.metadata_capture, self.book_capture
        for capture in (metadata, book):
            if capture.data_mode != DataMode.OBSERVED or capture.origin_capture_id is not None:
                raise ValueError("original_observed_capture_required")
            if len(capture.body.encode()) > 100_000:
                raise ValueError("replay_capture_size_exceeded")
        try:
            CaptureArchive(source=Source.POLYMARKET, captures=(metadata, book))
        except TransportError as exc:
            raise ValueError("replay_capture_request_not_allowed") from exc
        _validate_capture(metadata, "/markets", dict(metadata.params))
        _validate_capture(book, "/book", {"token_id": self.instrument.native_id})
        if metadata.received_at_utc > book.received_at_utc:
            raise ValueError("metadata_not_available_at_book_receipt")
        instrument = self.instrument
        if (
            instrument.source != Source.POLYMARKET
            or instrument.venue != "polymarket"
            or instrument.kind != "prediction"
            or instrument.base_unit != "contracts"
            or instrument.cash_unit != COLLATERAL_UNIT
        ):
            raise ValueError("replay_native_units_required")
        matches: list[tuple[str, str]] = []
        for raw in _array(metadata.payload()):
            row = _object(raw)
            condition = _text(row.get("conditionId"))
            _text(row.get("id"))
            outcomes = [_text(x) for x in _array(row.get("outcomes"))]
            tokens = [_text(x) for x in _array(row.get("clobTokenIds"))]
            if (
                len(tokens) != len(outcomes)
                or len(tokens) < 2
                or len(set(tokens)) != len(tokens)
                or len(set(outcomes)) != len(outcomes)
                or any(not x.isascii() or not x.isdigit() for x in tokens)
            ):
                raise ValueError("replay_metadata_identity_invalid")
            matches.extend(
                (condition, outcome)
                for token, outcome in zip(tokens, outcomes, strict=True)
                if token == instrument.native_id
            )
        if matches != [(instrument.market_id, instrument.outcome)]:
            raise ValueError("replay_native_identity_mismatch")
        _ = self.resolution_terms_hash
        raw_book = _object(book.payload())
        if (
            raw_book.get("asset_id") != instrument.native_id
            or raw_book.get("market") != instrument.market_id
        ):
            raise ValueError("replay_book_identity_mismatch")
        bids, asks = _levels(raw_book.get("bids")), _levels(raw_book.get("asks"))
        if not bids or not asks or raw_book.get("timestamp") is None:
            raise ValueError("replay_timestamp_and_two_sided_depth_required")
        event_time = _timestamp(raw_book["timestamp"])
        observation = Observation(
            capture_id=book.capture_id,
            instrument=Instrument(
                source=Source.POLYMARKET,
                venue="polymarket",
                native_id=instrument.native_id,
                kind="prediction",
                market_id=instrument.market_id,
                outcome=instrument.outcome,
            ),
            event_time_utc=event_time,
            received_at_utc=book.received_at_utc,
            quality=DataQuality.VALID,
            bids=bids,
            asks=asks,
            price_currency="collateral_units_per_contract",
        ).assessed()
        if observation.quality != DataQuality.VALID:
            raise ValueError("replay_source_time_not_qualified")
        return ExecutionQuote(
            snapshot_id=uuid5(book.capture_id, "M4-REPLAY-v1"),
            instrument=instrument,
            event_time_utc=event_time,
            received_at_utc=book.received_at_utc,
            quality=observation.quality,
            data_mode=DataMode.REPLAY,
            bids=bids,
            asks=asks,
        )


class ReplayManifest(FrozenModel):
    schema_version: Literal["1"] = "1"
    entries: tuple[ReplayEntry, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_unique(self) -> ReplayManifest:
        if len(self.model_dump_json().encode()) > 2_000_000:
            raise ValueError("replay_manifest_size_exceeded")
        books = [entry.book_capture.capture_id for entry in self.entries]
        if len(books) != len(set(books)):
            raise ValueError("duplicate_replay_book")
        native_events: set[tuple[Source, str, str, str]] = set()
        for entry in self.entries:
            quote = entry.quote
            # Capture UUIDs describe delivery, not new executable source depth.
            event = (
                quote.instrument.source,
                quote.instrument.venue,
                quote.instrument.native_id,
                str(quote.event_time_utc),
            )
            if event in native_events:
                raise ValueError("duplicate_native_book_event")
            native_events.add(event)
        originals: dict[str, str] = {}
        for entry in self.entries:
            for capture in (entry.metadata_capture, entry.book_capture):
                key = str(capture.capture_id)
                serialized = capture.model_dump_json()
                if key in originals and originals[key] != serialized:
                    raise ValueError("conflicting_original_capture")
                originals[key] = serialized
        return self

    @property
    def quotes(self) -> tuple[ExecutionQuote, ...]:
        return tuple(entry.quote for entry in self.entries)

    def allows(self, quote: ExecutionQuote) -> bool:
        return quote.data_mode == DataMode.REPLAY and quote in self.quotes
