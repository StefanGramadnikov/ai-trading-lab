"""F-01: entry limits cannot prevent reducing exits or liquidation valuations."""

from datetime import timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.experiments.runner import evaluate_cell
from ai_trading_lab.paper_execution.accounting import admit, apply_fill
from ai_trading_lab.strategies.models import StrategyFrame
from tests.backtest_helpers import research
from tests.experiment_helpers import experiment
from tests.unit.test_paper_accounting import intent, opening, policy, result
from tests.unit.test_runtime_engine import config, reducer, tick


def rising_frame(seconds: int, bid: str, probability: str | None) -> StrategyFrame:
    first = research().frames[0]
    at = first.at_utc + timedelta(seconds=seconds)
    quote = first.quote.model_copy(
        update={
            "snapshot_id": uuid4(),
            "event_time_utc": at,
            "received_at_utc": at,
            "bids": (first.quote.bids[0].model_copy(update={"price": Decimal(bid)}),),
            "asks": (
                first.quote.asks[0].model_copy(update={"price": Decimal(bid) + Decimal("0.01")}),
            ),
        }
    )
    assert first.forecast is not None
    forecast = (
        None
        if probability is None
        else first.forecast.model_copy(
            update={
                "probability": Decimal(probability),
                "produced_at_utc": at,
                "available_at_utc": at,
            }
        )
    )
    return first.model_copy(update={"at_utc": at, "quote": quote, "forecast": forecast})


def test_f01_reducing_sale_above_entry_cap_is_admitted() -> None:
    state, _ = opening("100")
    state, _ = apply_fill(state, intent(quantity="20", price="0.50"), result("20", "10", "0"))
    assert admit(state, intent("sell", "20", "0.60"), policy(max_order_notional="10")) is None
    assert (
        admit(state, intent("buy", "20", "0.60"), policy(max_order_notional="10"))
        == "max_order_notional"
    )


def test_f01_profitable_runtime_exit_above_cap_closes_position() -> None:
    cfg = config()
    first = research().frames[0]
    state = reducer(cfg, (tick(first, 1), tick(rising_frame(1, "0.60", "0.1"), 2)))
    assert not state["portfolio"]["positions"]
    assert Decimal(state["portfolio"]["realized_gross"]) == Decimal("2")
    assert Decimal(state["nav"]) == Decimal("102")


def test_f01_peak_nav_above_entry_cap_drives_drawdown() -> None:
    cfg = config()
    cfg["profile"]["drawdown_fraction"] = "0.01"
    first = tick(research().frames[0], 1)
    high = tick(rising_frame(1, "0.70", None), 2)
    state = reducer(cfg, (first, high))
    assert Decimal(state["peak_nav"]) == Decimal("104")
    assert Decimal(state["nav"]) == Decimal("104")
    fallen = reducer(cfg, (first, high, tick(rising_frame(2, "0.60", None), 3)))
    assert fallen["risk_latch"] == "drawdown_limit"
    assert Decimal(fallen["nav"]) == Decimal("102")


@pytest.mark.asyncio
async def test_f01_experiment_profitable_exit_and_marks_above_cap() -> None:
    spec = experiment()
    cell = spec.cells[0]
    frames = (
        cell.research.frames[0],
        rising_frame(1, "0.60", None),
        rising_frame(2, "0.60", "0.1"),
    )
    tape = cell.research.model_copy(update={"frames": frames, "end_at_utc": frames[-1].at_utc})
    evaluated = await evaluate_cell(
        spec, cell.model_copy(update={"rules": tape}), cast(AsyncEngine, None)
    )
    assert not evaluated.history.state.positions
    assert evaluated.history.state.realized_gross == Decimal("2")
    assert evaluated.report["nav"] is not None
    assert evaluated.report["suspension_reason"] is None
