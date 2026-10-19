from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_trading_lab.market_data.models import DataMode, DataQuality, Source
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    PaperInstrument,
    SimulationPolicy,
    decimal_amount,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def instrument(**changes: object) -> PaperInstrument:
    return PaperInstrument.model_validate(
        {
            "source": Source.POLYMARKET,
            "venue": "fixture",
            "native_id": "token-yes",
            "kind": "prediction",
            "base_unit": "token-yes",
            "cash_unit": "USD",
            "market_id": "market-1",
            "outcome": "yes",
            "tick_size": "0.01",
            "quantity_step": "0.1",
            "cash_step": "0.01",
            **changes,
        }
    )


def policy(**changes: object) -> SimulationPolicy:
    return SimulationPolicy.model_validate(
        {
            "version": "fixture-v1",
            "fee_bps": "10",
            "latency_ms": 100,
            "max_age_seconds": 60,
            "future_skew_seconds": 0,
            "max_order_notional": "1000",
            "max_position_notional": "1000",
            "max_total_exposure": "1000",
            "max_cost_bps": "10000",
            **changes,
        }
    )


def intent(**changes: object) -> OrderIntent:
    return OrderIntent.model_validate(
        {
            "portfolio_id": UUID(int=1),
            "idempotency_key": "intent-1",
            "instrument": instrument(),
            "side": "buy",
            "quantity": "2",
            "limit_price": "0.60",
            "created_at_utc": NOW,
            "expires_at_utc": NOW + timedelta(minutes=1),
            **changes,
        }
    )


def quote(**changes: object) -> ExecutionQuote:
    return ExecutionQuote.model_validate(
        {
            "snapshot_id": UUID(int=2),
            "instrument": instrument(),
            "event_time_utc": NOW,
            "received_at_utc": NOW,
            "quality": DataQuality.VALID,
            "data_mode": DataMode.FIXTURE,
            "bids": [{"price": "0.49", "quantity": "10"}],
            "asks": [{"price": "0.50", "quantity": "10"}],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "value",
    [True, False, 0.1, "NaN", "Infinity", "-Infinity", "1e19", "0.0000000000000000001", None],
)
def test_amount_rejects_inexact_or_unbounded_values(value: object) -> None:
    with pytest.raises(ValueError):
        decimal_amount(value)


def test_amount_validation_independent_of_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        assert decimal_amount("1000000000000000000") == Decimal("1e18")
        assert decimal_amount("0.123456789123456789000") == Decimal("0.123456789123456789")
        with pytest.raises(ValueError):
            decimal_amount("1000000000000000000.01")


@pytest.mark.parametrize(
    "changes",
    [
        {"base_unit": "USD"},
        {"market_id": None},
        {"outcome": None},
        {"tick_size": "0"},
        {"quantity_step": "-1"},
        {"cash_step": 0.01},
        {"native_id": ""},
        {"tick_size": "2"},
        {"extra": True},
    ],
)
def test_instrument_identity_and_units(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        instrument(**changes)


def test_frozen_contracts_and_canonical_hashes() -> None:
    first = instrument(tick_size="0.01")
    assert first.key == instrument(tick_size="0.0100").key
    assert first.key != instrument(native_id="token-no").key
    assert intent(quantity="2").fingerprint == intent(quantity="2.0").fingerprint
    assert intent().fingerprint != intent(side="sell").fingerprint
    attribute = "cash_unit"
    with pytest.raises(ValidationError):
        setattr(first, attribute, "EUR")


@pytest.mark.parametrize(
    "changes",
    [
        {"created_at_utc": NOW.replace(tzinfo=None)},
        {"expires_at_utc": NOW},
        {"quantity": "0"},
        {"limit_price": "1.01"},
        {"quantity": True},
    ],
)
def test_invalid_intents(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        intent(**changes)


def test_policy_has_no_financial_defaults() -> None:
    with pytest.raises(ValidationError):
        SimulationPolicy.model_validate({"version": "empty"})
    for changes in ({"fee_bps": "-1"}, {"latency_ms": True}, {"max_age_seconds": -1}):
        with pytest.raises(ValidationError):
            policy(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"asks": [{"price": 0.5, "quantity": "1"}]},
        {"asks": [{"price": "0.5", "quantity": True}]},
        {"asks": [{"price": "1.01", "quantity": "1"}]},
        {"asks": [{"price": "0.5", "quantity": "1e19"}]},
        {"asks": [{"price": "0.5", "quantity": "1"}] * 101},
        {"spot_price": "NaN"},
        {"impact_bps": "-1"},
    ],
)
def test_quote_bounds(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        quote(**changes)


@pytest.mark.parametrize(
    "value", ["0E-999999999", "0E+999999999", "1E+999999999", "0." + "0" * 201, "1." + "0" * 101]
)
def test_extreme_decimal_representation_rejected_before_hashing(value: str) -> None:
    with pytest.raises(ValueError):
        decimal_amount(value)


def test_zero_is_canonical_and_negative_amount_rejected() -> None:
    assert decimal_amount("-0.0000").as_tuple() == Decimal(0).as_tuple()
    assert instrument(tick_size="0.01").key == instrument(tick_size="0.010").key
    with pytest.raises(ValidationError):
        intent(quantity="-1")


@pytest.mark.parametrize(
    "changes",
    [
        {"latency_ms": 60001},
        {"max_age_seconds": 86401},
        {"future_skew_seconds": 61},
        {"max_cost_bps": "10001"},
    ],
)
def test_policy_operational_bounds(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        policy(**changes)
