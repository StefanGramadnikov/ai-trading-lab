"""Chronological synthetic proposals exercise real M3 safety/accounting."""

from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.backtesting.runner import evaluate
from ai_trading_lab.market_data.models import DataMode
from ai_trading_lab.strategies.models import CashConfig
from tests.backtest_helpers import research


def test_reproducible_rules_and_cash_comparison() -> None:
    spec = research()
    first, history = evaluate(spec)
    assert evaluate(spec)[0] == first
    assert first.final_state.cash == 90
    assert first.final_state.positions[0].quantity == 20
    assert first.decisions[0].receipt is not None
    assert len(history.effects) == 1
    assert (
        spec.dataset_hash
        == spec.model_copy(update={"strategy": CashConfig(version="v1")}).dataset_hash
    )
    cash, _ = evaluate(spec.model_copy(update={"strategy": CashConfig(version="v1")}))
    assert cash.final_state.cash == 100 and not cash.final_state.positions
    with localcontext() as context:
        context.prec = 6
        assert evaluate(spec)[0] == first


def test_partial_depth_and_repeat_cannot_reuse_consumption() -> None:
    spec = research()
    frame = spec.frames[0]
    snapshot = frame.quote.model_copy(
        update={"asks": (frame.quote.asks[0].model_copy(update={"quantity": Decimal(1)}),)}
    )
    frame = frame.model_copy(update={"quote": snapshot})
    result, _ = evaluate(spec.model_copy(update={"frames": (frame, frame)}))
    first, second = result.decisions
    assert first.receipt is not None and first.receipt.result.status == "PARTIALLY_FILLED"
    # Held probability strategy declines another entry, so no duplicate fill.
    assert second.decision.kind == "abstain"
    assert result.final_state.positions[0].quantity == 1


def test_unavailable_forecast_and_quote_do_not_trade() -> None:
    spec = research()
    frame = spec.frames[0]
    assert frame.forecast is not None
    for changed in (
        frame.model_copy(
            update={
                "forecast": frame.forecast.model_copy(
                    update={"available_at_utc": frame.at_utc + timedelta(seconds=1)}
                )
            }
        ),
        frame.model_copy(
            update={
                "quote": frame.quote.model_copy(
                    update={"received_at_utc": frame.at_utc + timedelta(seconds=1)}
                )
            }
        ),
    ):
        result, _ = evaluate(spec.model_copy(update={"frames": (changed,)}))
        assert result.decisions[0].decision.kind == "abstain"
        assert not result.final_state.positions


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"order_notional": Decimal("0.001")}, "quantity_below_step_or_no_position"),
        ({"order_notional": Decimal("1e18")}, "quantity_out_of_bounds"),
        ({"order_notional": Decimal("10000")}, None),
    ],
)
def test_sizing_or_risk_refusal(mutation: dict[str, object], reason: str | None) -> None:
    spec = research().model_copy(update=mutation)
    result, _ = evaluate(spec)
    record = result.decisions[0]
    assert record.sizing_reason == reason
    if reason is None:
        assert record.receipt is not None and record.receipt.result.status == "REJECTED"
    assert not result.final_state.positions


def test_expiry_and_failed_exit_are_recorded() -> None:
    spec = research()
    first = spec.frames[0]
    assert first.forecast is not None
    second = first.model_copy(
        update={
            "at_utc": first.at_utc + timedelta(seconds=1),
            "forecast": first.forecast.model_copy(update={"probability": Decimal("0.1")}),
        }
    )
    # The exit proposal reaches the engine but configured order cap refuses it.
    result, _ = evaluate(
        spec.model_copy(
            update={
                "frames": (first, second),
                "simulation": spec.simulation.model_copy(update={"max_cost_bps": Decimal(0)}),
            }
        )
    )
    assert result.decisions[0].receipt is not None
    assert result.decisions[0].receipt.result.status == "REJECTED"
    expiry, _ = evaluate(
        spec.model_copy(
            update={
                "simulation": spec.simulation.model_copy(update={"latency_ms": 1000}),
                "order_ttl_seconds": 1,
            }
        )
    )
    assert expiry.decisions[0].receipt is not None
    assert expiry.decisions[0].receipt.result.status == "EXPIRED"


def test_tape_bounds_modes_chronology_snapshot_and_identity_hashes() -> None:
    spec = research()
    data = spec.model_dump(mode="json")
    assert ResearchInput.model_validate(data) == spec
    for update in (
        {"start_at_utc": spec.end_at_utc + timedelta(seconds=1)},
        {"frames": ()},
        {"frames": (spec.frames[0],) * 101},
        {"end_at_utc": spec.start_at_utc - timedelta(seconds=1)},
        {"data_mode": "OBSERVED"},
        {
            "frames": (
                spec.frames[0].model_copy(
                    update={
                        "quote": spec.frames[0].quote.model_copy(
                            update={"data_mode": DataMode.REPLAY}
                        )
                    }
                ),
            )
        },
        {
            "frames": (
                spec.frames[0],
                spec.frames[0].model_copy(
                    update={"quote": spec.frames[0].quote.model_copy(update={"asks": ()})}
                ),
            )
        },
        {"simulation": spec.simulation.model_copy(update={"latency_ms": 2000})},
    ):
        with pytest.raises(ValueError):
            evaluate(spec.model_copy(update=update))
    assert spec.fingerprint != spec.model_copy(update={"order_notional": Decimal(11)}).fingerprint
    assert (
        spec.dataset_hash != spec.model_copy(update={"frames": (spec.frames[0],) * 2}).dataset_hash
    )


def test_failed_sell_leaves_position_open_and_records_cancelled_order() -> None:
    from uuid import uuid4

    spec = research()
    first = spec.frames[0]
    assert first.forecast is not None
    quote = first.quote.model_copy(
        update={
            "snapshot_id": uuid4(),
            "bids": (first.quote.bids[0].model_copy(update={"quantity": Decimal("0.001")}),),
        }
    )
    second = first.model_copy(
        update={
            "quote": quote,
            "at_utc": first.at_utc + timedelta(seconds=1),
            "forecast": first.forecast.model_copy(update={"probability": Decimal("0.1")}),
        }
    )
    result, _ = evaluate(spec.model_copy(update={"frames": (first, second)}))
    exit_record = result.decisions[1]
    assert exit_record.decision.kind == "proposal" and exit_record.decision.side == "sell"
    assert exit_record.receipt is not None
    assert exit_record.receipt.result.status == "CANCELLED"
    assert result.final_state.positions[0].quantity == 20


def test_replay_tape_raw_resolution_and_manifest_binding() -> None:
    from ai_trading_lab.paper_execution.replay import COLLATERAL_UNIT
    from tests.unit.test_paper_replay import registered

    fixture = research()
    manifest = registered()
    entry = manifest.entries[0]
    quote = entry.quote
    data = fixture.model_dump(mode="json")
    data.update(
        data_mode="REPLAY",
        cash_unit=COLLATERAL_UNIT,
        replay_manifest=manifest.model_dump(mode="json"),
    )
    frame = data["frames"][0]
    frame["quote"] = quote.model_dump(mode="json")
    frame["resolution_terms_hash"] = entry.resolution_terms_hash
    frame["forecast"].update(
        instrument_key=quote.instrument.key,
        market_id=quote.instrument.market_id,
        outcome=quote.instrument.outcome,
        data_mode="REPLAY",
        resolution_terms_hash=entry.resolution_terms_hash,
    )
    spec = ResearchInput.model_validate(data)
    result, _ = evaluate(spec)
    assert result.decisions[0].receipt is not None
    assert result.decisions[0].receipt.result.quantity > 0
    for mutation in (
        {"replay_manifest": None},
        {"frames": (spec.frames[0].model_copy(update={"resolution_terms_hash": "b" * 64}),)},
        {"data_mode": "FIXTURE", "frames": fixture.frames},
    ):
        with pytest.raises(ValueError):
            evaluate(spec.model_copy(update=mutation))


def test_tape_rejects_clock_reversal_and_price_below_tick() -> None:
    from ai_trading_lab.strategies.models import MomentumConfig, MomentumFeatures, PricePoint
    from tests.integration.test_paper_store import asset, quote

    spec = research()
    first = spec.frames[0]
    second = first.model_copy(update={"at_utc": first.at_utc - timedelta(seconds=1)})
    with pytest.raises(ValueError, match="chronological"):
        evaluate(spec.model_copy(update={"frames": (first, second)}))
    spot = quote(asset(), price="0.001")
    key = spot.instrument.key
    at = first.at_utc
    frame = first.model_copy(
        update={
            "quote": spot,
            "forecast": None,
            "momentum": MomentumFeatures(
                instrument_key=key,
                data_mode="FIXTURE",
                event_time_utc=at,
                available_at_utc=at,
                volume_6h="100",
                volume_24h="100",
                pool_created_at_utc=at,
                liquidity="100",
                liquidity_unit="USD",
            ),
            "history": (
                PricePoint(
                    instrument_key=key,
                    data_mode="FIXTURE",
                    price="0.0001",
                    event_time_utc=at - timedelta(seconds=1),
                    available_at_utc=at - timedelta(seconds=1),
                ),
            ),
        }
    )
    config = MomentumConfig(
        version="test",
        max_age_seconds=60,
        lookback_seconds=1,
        min_momentum_bps="0",
        min_volume_ratio="0",
        max_pool_age_seconds=60,
        min_liquidity="1",
        max_owner_share=None,
        exit_volume_ratio="0",
    )
    result, _ = evaluate(spec.model_copy(update={"strategy": config, "frames": (frame,)}))
    assert result.decisions[0].sizing_reason == "price_below_tick"


@pytest.mark.parametrize("name", ["probability", "momentum"])
def test_documented_synthetic_examples_have_entry_and_exit(name: str) -> None:
    from pathlib import Path

    from ai_trading_lab.backtesting.cli import read_research

    spec = read_research(Path(f"tests/fixtures/research_{name}.json"))
    result, _ = evaluate(spec)
    assert len(result.decisions) == 2
    assert all(
        r.receipt is not None and r.receipt.result.status == "FILLED" for r in result.decisions
    )
    assert not result.final_state.positions
    assert result.final_state.fees > 0
