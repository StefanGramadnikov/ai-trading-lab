"""Synthetic research signals test deterministic rules, never investment returns."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_trading_lab.market_data.models import DataMode, DataQuality, PriceLevel, Source
from ai_trading_lab.paper_execution.accounting import PortfolioState, Position
from ai_trading_lab.paper_execution.models import ExecutionQuote, PaperInstrument
from ai_trading_lab.strategies.models import (
    Abstain,
    CashConfig,
    Forecast,
    MomentumConfig,
    MomentumFeatures,
    PricePoint,
    ProbabilityConfig,
    StrategyFrame,
    TradeProposal,
)
from ai_trading_lab.strategies.rules import decide

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def instrument(prediction: bool = False) -> PaperInstrument:
    return PaperInstrument(
        source=Source.POLYMARKET if prediction else Source.DEXSCREENER,
        venue="synthetic",
        native_id="native",
        kind="prediction" if prediction else "spot",
        base_unit="contract" if prediction else "token",
        cash_unit="USD",
        market_id="market" if prediction else None,
        outcome="YES" if prediction else None,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("1"),
        cash_step=Decimal("0.01"),
    )


def state(held: bool = False, prediction: bool = False) -> PortfolioState:
    return PortfolioState(
        portfolio_id=uuid4(),
        cash_unit="USD",
        cash_step=Decimal("0.01"),
        initial_cash=Decimal(100),
        cash=Decimal(90 if held else 100),
        positions=(
            Position(
                instrument=instrument(prediction), quantity=Decimal(1), cost_basis=Decimal(10)
            ),
        )
        if held
        else (),
    )


def frame(prediction: bool = False) -> StrategyFrame:
    asset = instrument(prediction)
    return StrategyFrame(
        at_utc=NOW,
        resolution_terms_hash="a" * 64 if prediction else None,
        quote=ExecutionQuote(
            snapshot_id=uuid4(),
            instrument=asset,
            event_time_utc=NOW,
            received_at_utc=NOW,
            quality=DataQuality.VALID,
            data_mode=DataMode.FIXTURE,
            spot_price=None if prediction else Decimal(11),
            bids=(PriceLevel(price=Decimal("0.40"), quantity=Decimal(10)),) if prediction else (),
            asks=(PriceLevel(price=Decimal("0.50"), quantity=Decimal(10)),) if prediction else (),
        ),
        history=(
            PricePoint(
                instrument_key=asset.key,
                data_mode=DataMode.FIXTURE,
                price=Decimal(10),
                event_time_utc=NOW - timedelta(seconds=60),
                available_at_utc=NOW - timedelta(seconds=60),
            ),
        ),
        momentum=MomentumFeatures(
            instrument_key=asset.key,
            data_mode=DataMode.FIXTURE,
            event_time_utc=NOW,
            available_at_utc=NOW,
            volume_6h=Decimal(25),
            volume_24h=Decimal(100),
            pool_created_at_utc=NOW - timedelta(seconds=120),
            liquidity=Decimal(1000),
            liquidity_unit="USD",
        ),
        forecast=Forecast(
            instrument_key=asset.key,
            market_id="market",
            outcome="YES",
            resolution_terms_hash="a" * 64,
            probability=Decimal("0.70"),
            provenance="synthetic-fixture",
            version="v1",
            calibration_reference="synthetic-no-real-calibration",
            independent=True,
            data_mode=DataMode.FIXTURE,
            produced_at_utc=NOW,
            available_at_utc=NOW,
        )
        if prediction
        else None,
    )


def momentum(**updates: object) -> MomentumConfig:
    values: dict[str, object] = dict(
        version="v1",
        max_age_seconds=10,
        lookback_seconds=60,
        min_momentum_bps="1000",
        min_volume_ratio="1",
        max_pool_age_seconds=300,
        min_liquidity="1000",
        max_owner_share=None,
        exit_volume_ratio="0.2",
    )
    values.update(updates)
    return MomentumConfig.model_validate(values)


def probability() -> ProbabilityConfig:
    return ProbabilityConfig(
        version="v1",
        max_age_seconds=10,
        min_edge=Decimal("0.1"),
        cost_buffer=Decimal("0.1"),
    )


def test_momentum_entry_equality_is_exact_and_context_independent() -> None:
    with localcontext() as context:
        context.prec = 2
        decision = decide(momentum(), frame(), state())
    assert isinstance(decision, TradeProposal)
    assert decision.side == "buy" and decision.limit_price == 11
    assert len(decision.evidence) == 3


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"momentum": None}, "momentum_features_required"),
        ({"history": ()}, "price_history_unqualified"),
    ],
)
def test_missing_inputs(change: dict[str, object], reason: str) -> None:
    assert decide(momentum(), frame().model_copy(update=change), state()).reason == reason


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"volume_6h": None}, "volume_required"),
        ({"volume_24h": None}, "volume_required"),
        ({"liquidity_unit": "EUR"}, "pool_liquidity_unqualified"),
        ({"liquidity": Decimal(999)}, "pool_liquidity_unqualified"),
        ({"pool_created_at_utc": NOW + timedelta(seconds=1)}, "pool_liquidity_unqualified"),
        ({"pool_created_at_utc": None}, "pool_liquidity_unqualified"),
        ({"pool_created_at_utc": NOW - timedelta(seconds=301)}, "pool_liquidity_unqualified"),
        ({"instrument_key": "f" * 64}, "momentum_evidence_unqualified"),
        ({"data_mode": DataMode.REPLAY}, "momentum_evidence_unqualified"),
        ({"available_at_utc": NOW + timedelta(seconds=1)}, "momentum_evidence_unqualified"),
        ({"event_time_utc": NOW - timedelta(seconds=11)}, "momentum_evidence_unqualified"),
        ({"volume_6h": Decimal(24)}, "entry_threshold_not_met"),
    ],
)
def test_feature_failure_modes(changes: dict[str, object], reason: str) -> None:
    sample = frame()
    assert sample.momentum
    sample = sample.model_copy(update={"momentum": sample.momentum.model_copy(update=changes)})
    assert decide(momentum(), sample, state()).reason == reason


def test_owner_account_metrics_are_not_inferred_and_verified_threshold_is_inclusive() -> None:
    sample = frame()
    assert sample.momentum
    config = momentum(max_owner_share="0.5")
    assert decide(config, sample, state()).reason == "ownership_unqualified"
    feature = sample.momentum.model_copy(update={"verified_owner_share": Decimal("0.5")})
    assert decide(config, sample.model_copy(update={"momentum": feature}), state()).reason == (
        "ownership_unqualified"
    )
    feature = feature.model_copy(update={"ownership_basis": "unique_owner_balances"})
    assert isinstance(
        decide(config, sample.model_copy(update={"momentum": feature}), state()), TradeProposal
    )
    with pytest.raises(ValidationError):
        MomentumFeatures.model_validate({**feature.model_dump(), "holder_count": 42})


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_key": "f" * 64},
        {"data_mode": DataMode.REPLAY},
        {"available_at_utc": NOW + timedelta(seconds=1)},
        {"event_time_utc": NOW - timedelta(seconds=71)},
        {"price": Decimal(11)},
    ],
)
def test_history_missing_stale_future_or_no_momentum(changes: dict[str, object]) -> None:
    sample = frame()
    sample = sample.model_copy(update={"history": (sample.history[0].model_copy(update=changes),)})
    assert isinstance(decide(momentum(), sample, state()), Abstain)


def test_volume_exit_strict_boundary_and_no_reentry_while_held() -> None:
    sample = frame()
    assert sample.momentum
    for volume, expected in [
        (Decimal(5), "holding_without_exit"),
        (Decimal(4), "volume_exhaustion"),
    ]:
        changed = sample.model_copy(
            update={"momentum": sample.momentum.model_copy(update={"volume_6h": volume})}
        )
        assert decide(momentum(), changed, state(held=True)).reason == expected


def test_probability_entry_exit_and_equality_cost_buffer() -> None:
    sample = frame(True)
    decision = decide(probability(), sample, state(prediction=True))
    assert isinstance(decision, TradeProposal) and decision.side == "buy"
    assert sample.forecast
    changed = sample.model_copy(
        update={"forecast": sample.forecast.model_copy(update={"probability": Decimal("0.2")})}
    )
    decision = decide(probability(), changed, state(held=True, prediction=True))
    assert isinstance(decision, TradeProposal) and decision.side == "sell"
    assert isinstance(decide(probability(), sample, state(held=True, prediction=True)), Abstain)
    assert isinstance(decide(probability(), changed, state(prediction=True)), Abstain)


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_key": "f" * 64},
        {"market_id": "other"},
        {"resolution_terms_hash": "b" * 64},
        {"outcome": "NO"},
        {"independent": False},
        {"data_mode": DataMode.REPLAY},
        {"available_at_utc": NOW + timedelta(seconds=1)},
        {"produced_at_utc": NOW - timedelta(seconds=11)},
    ],
)
def test_forecast_identity_provenance_and_availability(changes: dict[str, object]) -> None:
    sample = frame(True)
    assert sample.forecast
    sample = sample.model_copy(update={"forecast": sample.forecast.model_copy(update=changes)})
    assert decide(probability(), sample, state(prediction=True)).reason == "forecast_unqualified"


@pytest.mark.parametrize(
    "changes",
    [
        {"data_mode": DataMode.OBSERVED},
        {"quality": DataQuality.PARTIAL},
        {"event_time_utc": None},
        {"event_time_utc": NOW - timedelta(seconds=11)},
        {"received_at_utc": NOW + timedelta(seconds=1)},
    ],
)
def test_quote_gate(changes: dict[str, object]) -> None:
    sample = frame()
    sample = sample.model_copy(update={"quote": sample.quote.model_copy(update=changes)})
    assert decide(momentum(), sample, state()).reason == "quote_unqualified"


def test_forecast_missing_bad_book_and_wrong_market_kind() -> None:
    sample = frame(True)
    assert (
        decide(
            probability(), sample.model_copy(update={"forecast": None}), state(prediction=True)
        ).reason
        == "forecast_required"
    )
    quote = sample.quote.model_copy(update={"bids": ()})
    assert (
        decide(
            probability(), sample.model_copy(update={"quote": quote}), state(prediction=True)
        ).reason
        == "prediction_book_required"
    )
    quote = sample.quote.model_copy(
        update={"bids": (PriceLevel(price=Decimal("0.6"), quantity=Decimal(1)),)}
    )
    assert (
        decide(
            probability(), sample.model_copy(update={"quote": quote}), state(prediction=True)
        ).reason
        == "prediction_book_unqualified"
    )
    assert decide(momentum(), sample, state(prediction=True)).reason == "spot_price_required"


def test_contract_immutability_exact_numbers_required_config_and_utc() -> None:
    sample = frame()
    for attribute in ("at_utc", "history"):
        with pytest.raises(ValidationError):
            setattr(sample, attribute, NOW)
    with pytest.raises(ValidationError):
        momentum(min_liquidity=1.0)
    with pytest.raises(ValidationError):
        MomentumConfig.model_validate({"version": "v1"})
    with pytest.raises(ValidationError):
        StrategyFrame.model_validate({**sample.model_dump(), "at_utc": "2026-01-01T00:00:00"})
    assert decide(CashConfig(version="v1"), sample, state()).reason == "cash_benchmark"
    changed = state().model_copy(update={"cash_unit": "EUR"})
    assert decide(momentum(), sample, changed).reason == "portfolio_unit_mismatch"


def test_resolution_terms_must_be_bound_and_hashes_are_validated() -> None:
    sample = frame(True)
    assert (
        decide(
            probability(),
            sample.model_copy(update={"resolution_terms_hash": None}),
            state(prediction=True),
        ).reason
        == "forecast_unqualified"
    )
    assert sample.forecast
    with pytest.raises(ValidationError):
        Forecast.model_validate({**sample.forecast.model_dump(), "resolution_terms_hash": "bad"})
    with pytest.raises(ValidationError):
        StrategyFrame.model_validate({**sample.model_dump(), "resolution_terms_hash": "bad"})


@pytest.mark.parametrize(
    "change",
    [
        {"price": Decimal(9)},
        {"available_at_utc": NOW - timedelta(seconds=59)},
    ],
)
def test_conflicting_history_is_rejected_in_either_order(change: dict[str, object]) -> None:
    sample = frame()
    first = sample.history[0]
    other = first.model_copy(update=change)
    for history in ((first, other), (other, first)):
        assert decide(
            momentum(), sample.model_copy(update={"history": history}), state()
        ).reason == ("conflicting_price_history")
    assert isinstance(
        decide(momentum(), sample.model_copy(update={"history": (first, first)}), state()),
        TradeProposal,
    )
