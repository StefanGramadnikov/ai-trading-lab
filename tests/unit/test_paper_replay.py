"""Synthetic raw captures test qualification consistency, never historical truth."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_trading_lab.market_data.models import Capture, DataMode
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import OrderIntent, PaperInstrument
from ai_trading_lab.paper_execution.replay import COLLATERAL_UNIT, ReplayEntry, ReplayManifest
from tests.integration.test_paper_store import policy

NOW = datetime(2026, 10, 19, tzinfo=UTC)
D = Decimal


def entry_data() -> dict[str, object]:
    instrument = PaperInstrument(
        source="polymarket",
        venue="polymarket",
        native_id="123",
        kind="prediction",
        base_unit="contracts",
        cash_unit=COLLATERAL_UNIT,
        market_id="condition",
        outcome="Yes",
        tick_size="0.01",
        quantity_step="1",
        cash_step="0.01",
    )
    metadata = Capture(
        source="polymarket",
        method="GET",
        path="/markets",
        params={"active": "true", "closed": "false", "limit": "5", "offset": "0"},
        body=json.dumps(
            [
                {
                    "id": "id",
                    "conditionId": "condition",
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["123", "456"],
                    "description": "TEST synthetic resolution description; not market evidence",
                }
            ]
        ),
        status_code=200,
        received_at_utc=NOW,
        data_mode=DataMode.OBSERVED,
    )
    book = Capture(
        source="polymarket",
        method="GET",
        path="/book",
        params={"token_id": "123"},
        body=json.dumps(
            {
                "asset_id": "123",
                "market": "condition",
                "timestamp": "1792368000000",
                "bids": [{"price": "0.49", "size": "10"}],
                "asks": [{"price": "0.51", "size": "10"}],
            }
        ),
        status_code=200,
        received_at_utc=NOW,
        data_mode=DataMode.OBSERVED,
    )
    return dict(
        metadata_capture=metadata.model_dump(mode="json"),
        book_capture=book.model_dump(mode="json"),
        instrument=instrument.model_dump(mode="json"),
        assumption_version="TEST-unverified-v1",
    )


def registered() -> ReplayManifest:
    return ReplayManifest(entries=(ReplayEntry.model_validate(entry_data()),))


def config(manifest: ReplayManifest | None = None) -> dict[str, object]:
    result: dict[str, object] = dict(
        cash_unit=COLLATERAL_UNIT,
        cash_step="0.01",
        initial_cash="100",
        policy=policy().model_dump(mode="json"),
    )
    if manifest is not None:
        result["replay_manifest"] = manifest.model_dump(mode="json")
    return result


def test_raw_derived_quote_manifest_roundtrip_and_exact_match() -> None:
    manifest = registered()
    quote = manifest.quotes[0]
    assert quote.data_mode == DataMode.REPLAY
    assert quote.asks[0].price == D("0.51")
    assert quote.event_time_utc == NOW
    assert ReplayManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert manifest.allows(quote)
    assert not manifest.allows(quote.model_copy(update={"snapshot_id": uuid4()}))
    assert not manifest.allows(
        quote.model_copy(update={"event_time_utc": NOW + timedelta(seconds=1)})
    )
    assert not manifest.allows(quote.model_copy(update={"data_mode": DataMode.OBSERVED}))
    with pytest.raises(ValidationError):
        manifest.entries[0].assumption_version = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_unit", "USD"),
        ("base_unit", "YES"),
        ("native_id", "456"),
        ("outcome", "No"),
        ("market_id", "other"),
        ("venue", "other"),
    ],
)
def test_forged_identity_or_units_rejected(field: str, value: str) -> None:
    raw = entry_data()
    instrument = dict(cast(dict[str, object], raw["instrument"]))
    instrument[field] = value
    raw["instrument"] = instrument
    with pytest.raises(ValueError):
        ReplayEntry.model_validate(raw)


@pytest.mark.parametrize("capture_field", ["metadata_capture", "book_capture"])
@pytest.mark.parametrize("mode", ["FIXTURE", "REPLAY"])
def test_original_fixture_and_replay_captures_cannot_be_upgraded(
    capture_field: str, mode: str
) -> None:
    raw = entry_data()
    capture = dict(cast(dict[str, object], raw[capture_field]))
    capture["data_mode"] = mode
    raw[capture_field] = capture
    with pytest.raises(ValueError, match="original_observed_capture_required"):
        ReplayEntry.model_validate(raw)


@pytest.mark.parametrize("change", ["missing_time", "stale", "future", "empty", "wrong_native"])
def test_raw_book_qualification_failure(change: str) -> None:
    raw = entry_data()
    capture = dict(cast(dict[str, object], raw["book_capture"]))
    book = json.loads(str(capture["body"]))
    if change == "missing_time":
        del book["timestamp"]
    elif change == "stale":
        book["timestamp"] = "1792367900000"
    elif change == "future":
        book["timestamp"] = "1792368100000"
    elif change == "empty":
        book["asks"] = []
    else:
        book["asset_id"] = "999"
    capture["body"] = json.dumps(book)
    raw["book_capture"] = capture
    with pytest.raises(ValueError):
        ReplayEntry.model_validate(raw)


def test_future_metadata_duplicate_book_and_conflicting_capture_rejected() -> None:
    raw = entry_data()
    capture = dict(cast(dict[str, object], raw["metadata_capture"]))
    capture["received_at_utc"] = (NOW + timedelta(seconds=1)).isoformat()
    raw["metadata_capture"] = capture
    with pytest.raises(ValueError, match="metadata_not_available"):
        ReplayEntry.model_validate(raw)
    one = registered().entries[0]
    with pytest.raises(ValueError, match="duplicate_replay_book"):
        ReplayManifest(entries=(one, one))
    second_raw = entry_data()
    second_meta = dict(cast(dict[str, object], second_raw["metadata_capture"]))
    second_meta["capture_id"] = str(one.metadata_capture.capture_id)
    second_meta["body"] = str(second_meta["body"]) + " "
    second_raw["metadata_capture"] = second_meta
    # Use another book event so this case specifically reaches capture-integrity
    # validation rather than the independent same-event depth-reuse rejection.
    second_book = dict(cast(dict[str, object], second_raw["book_capture"]))
    body = json.loads(str(second_book["body"]))
    body["timestamp"] = "1792368001000"
    second_book["body"] = json.dumps(body)
    second_book["received_at_utc"] = (NOW + timedelta(seconds=1)).isoformat()
    second_raw["book_capture"] = second_book
    with pytest.raises(ValueError, match="conflicting_original_capture"):
        ReplayManifest(entries=(one, ReplayEntry.model_validate(second_raw)))


@pytest.mark.parametrize("registered_manifest", [False, True])
def test_history_admission_consumption_and_observed_refusal(registered_manifest: bool) -> None:
    manifest = registered()
    quote = manifest.quotes[0]
    history = History(uuid4(), config(manifest if registered_manifest else None))
    for index, mode in enumerate((DataMode.REPLAY, DataMode.REPLAY, DataMode.OBSERVED)):
        intent = OrderIntent(
            portfolio_id=history.state.portfolio_id,
            idempotency_key=str(index),
            instrument=quote.instrument,
            side="buy",
            quantity="8",
            limit_price="0.60",
            created_at_utc=NOW,
            expires_at_utc=NOW + timedelta(seconds=1),
        )
        effect = history.apply(
            "order",
            str(index),
            {
                "intent": intent.model_dump(mode="json"),
                "quote": quote.model_copy(
                    update={
                        "data_mode": mode,
                        "snapshot_id": uuid4() if mode == DataMode.OBSERVED else quote.snapshot_id,
                    }
                ).model_dump(mode="json"),
            },
            NOW,
        )
        assert effect.receipt is not None
        result = effect.receipt.result
        if not registered_manifest or mode == DataMode.OBSERVED:
            assert result.reason == "fixture_mode_required"
        else:
            assert result.quantity == D("8" if index == 0 else "2")


def test_supplied_observation_or_quote_cannot_upgrade_raw_data() -> None:
    raw = entry_data()
    raw["quote"] = registered().quotes[0].model_dump(mode="json")
    with pytest.raises(ValueError, match="Extra inputs"):
        ReplayEntry.model_validate(raw)


@pytest.mark.parametrize("change", ["size", "schema", "origin", "status", "metadata", "source"])
def test_capture_envelope_and_metadata_fail_closed(change: str) -> None:
    raw = entry_data()
    capture = dict(cast(dict[str, object], raw["metadata_capture"]))
    if change == "size":
        capture["body"] = " " * 100_001
    elif change == "schema":
        capture["path"] = "/orders"
    elif change == "origin":
        capture["origin_capture_id"] = str(uuid4())
    elif change == "status":
        capture["status_code"] = 500
    elif change == "source":
        capture["source"] = "kalshi"
    else:
        rows = json.loads(str(capture["body"]))
        rows[0]["clobTokenIds"] = ["123", "123"]
        capture["body"] = json.dumps(rows)
    raw["metadata_capture"] = capture
    with pytest.raises(ValueError):
        ReplayEntry.model_validate(raw)


def test_replay_size_bound_and_price_registration() -> None:
    raw = entry_data()
    capture = dict(cast(dict[str, object], raw["book_capture"]))
    capture["body"] = str(capture["body"]) + " " * 99_000
    raw["book_capture"] = capture
    entry = ReplayEntry.model_validate(raw)
    entries = tuple(
        entry.model_copy(
            update={"book_capture": entry.book_capture.model_copy(update={"capture_id": uuid4()})}
        )
        for _ in range(21)
    )
    with pytest.raises(ValueError, match="replay_manifest_size_exceeded"):
        ReplayManifest(entries=entries)
    manifest = registered()
    quote = manifest.quotes[0]
    changed = quote.model_copy(
        update={"asks": (quote.asks[0].model_copy(update={"price": D("0.40")}),)}
    )
    assert not manifest.allows(changed)


def test_resolution_description_hash_is_raw_and_required() -> None:
    from ai_trading_lab.market_data.storage import content_hash

    entry = registered().entries[0]
    assert entry.resolution_terms_hash == content_hash(
        "TEST synthetic resolution description; not market evidence"
    )
    for description in (None, "", " "):
        raw = entry_data()
        capture = dict(cast(dict[str, object], raw["metadata_capture"]))
        rows = json.loads(str(capture["body"]))
        if description is None:
            del rows[0]["description"]
        else:
            rows[0]["description"] = description
        capture["body"] = json.dumps(rows)
        raw["metadata_capture"] = capture
        with pytest.raises(ValueError):
            ReplayEntry.model_validate(raw)


def test_different_capture_uuids_cannot_refresh_identical_native_book_depth() -> None:
    from uuid import uuid4

    manifest = registered()
    entry = manifest.entries[0]
    repeated = entry.model_copy(
        update={"book_capture": entry.book_capture.model_copy(update={"capture_id": uuid4()})}
    )
    with pytest.raises(ValueError, match="duplicate_native_book_event"):
        ReplayManifest(entries=(entry, repeated))
    # A changed instrument quantum must not turn the same native event into new depth.
    changed = repeated.model_copy(
        update={"instrument": entry.instrument.model_copy(update={"quantity_step": Decimal("0.1")})}
    )
    with pytest.raises(ValueError, match="duplicate_native_book_event"):
        ReplayManifest(entries=(entry, changed))
