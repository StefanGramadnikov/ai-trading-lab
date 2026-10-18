from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_trading_lab.market_data.models import (
    Capture,
    DataQuality,
    Instrument,
    Observation,
    PriceLevel,
    Source,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def test_capture_preserves_decimal_and_exact_body_fingerprint() -> None:
    capture = Capture(
        source=Source.DEXSCREENER,
        method="GET",
        path="/token-profiles/latest/v1",
        body='{"price":0.123456789012345678901}',
        received_at_utc=NOW,
    )
    assert capture.payload() == {"price": Decimal("0.123456789012345678901")}
    assert capture.fingerprint == capture.model_copy().fingerprint
    assert (
        capture.model_copy(update={"body": capture.body + " "}).fingerprint != capture.fingerprint
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_rejected(value: str) -> None:
    capture = Capture(
        source=Source.SOLANA, method="POST", path="/", body=value, received_at_utc=NOW
    )
    with pytest.raises(ValueError):
        capture.payload()


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-0.01"])
def test_invalid_price_level(price: str) -> None:
    with pytest.raises(ValidationError):
        PriceLevel(price=Decimal(price), quantity=Decimal(1))


def test_identity_and_time_validation() -> None:
    with pytest.raises(ValidationError):
        Instrument(source=Source.KALSHI, venue="kalshi", native_id="", kind="prediction")
    with pytest.raises(ValidationError):
        Capture(
            source=Source.SOLANA,
            method="POST",
            path="/",
            body="{}",
            received_at_utc=datetime(2026, 10, 18),
        )


@pytest.mark.parametrize(
    "age, quality", [(0, DataQuality.VALID), (61, DataQuality.STALE), (-6, DataQuality.INVALID)]
)
def test_freshness(age: int, quality: DataQuality) -> None:
    observation = Observation(
        capture_id=uuid4(),
        instrument=Instrument(
            source=Source.KALSHI, venue="kalshi", native_id="T:YES", kind="prediction"
        ),
        received_at_utc=NOW,
        event_time_utc=NOW - timedelta(seconds=age),
        quality=DataQuality.VALID,
    )
    assert observation.assessed().quality == quality


def test_missing_time_and_reassessment_are_explicit_and_idempotent() -> None:
    observation = Observation(
        capture_id=uuid4(),
        instrument=Instrument(source=Source.SOLANA, venue="solana", native_id="mint", kind="token"),
        received_at_utc=NOW,
    )
    first = observation.assessed()
    assert first.quality == DataQuality.PARTIAL
    assert first.event_time_utc is None
    assert first.assessed() == first


def test_nested_evidence_is_immutable_and_detached_from_input() -> None:
    from pydantic import JsonValue

    nested: dict[str, JsonValue] = {"nested": {"value": ["original"]}}
    instrument = Instrument(
        source=Source.SOLANA, venue="solana", native_id="mint", kind="token", metadata=nested
    )
    capture = Capture(
        source=Source.SOLANA,
        method="POST",
        path="/",
        request_body=nested,
        params={"key": "value"},
        body="{}",
        received_at_utc=NOW,
    )
    observation = Observation(
        capture_id=capture.capture_id, instrument=instrument, received_at_utc=NOW, facts=nested
    )
    original_hash = instrument.definition_hash
    nested["nested"] = "changed"
    for evidence in (instrument.metadata, capture.request_body, observation.facts):
        assert evidence is not None
        child = evidence["nested"]
        assert isinstance(child, dict)
        values = child["value"]
        assert isinstance(values, list)
        with pytest.raises(TypeError, match="immutable"):
            child["value"] = []
        with pytest.raises(TypeError, match="immutable"):
            values.append("changed")
        with pytest.raises(TypeError, match="immutable"):
            evidence.clear()
    with pytest.raises(TypeError, match="immutable"):
        capture.params["key"] = "changed"
    assert instrument.definition_hash == original_hash
    assert Instrument.model_validate_json(instrument.model_dump_json()) == instrument
    assert capture.model_copy(deep=True) == capture
    assert Observation.model_validate_json(observation.model_dump_json()) == observation


def test_default_evidence_containers_are_frozen() -> None:
    instrument = Instrument(source=Source.SOLANA, venue="solana", native_id="mint", kind="token")
    with pytest.raises(TypeError, match="immutable"):
        instrument.metadata["key"] = "changed"


@pytest.mark.parametrize("depth", [65, 10000])
def test_capture_rejects_excessive_json_depth_as_validation_error(depth: int) -> None:
    capture = Capture(
        source=Source.KALSHI,
        method="GET",
        path="/markets",
        body="[" * depth + "0" + "]" * depth,
        received_at_utc=NOW,
    )
    with pytest.raises(ValueError, match="safe depth"):
        capture.payload()


def test_json_depth_boundary_preserves_precise_values() -> None:
    from ai_trading_lab.market_data.models import parse_json

    value = parse_json("[" * 64 + "0.123456789012345678901" + "]" * 64)
    for _ in range(64):
        assert isinstance(value, list)
        value = value[0]
    assert value == Decimal("0.123456789012345678901")
