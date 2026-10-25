"""Folding a fill into the loop state is idempotent and only closes what is actually closed."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ai_trading_lab.live.engine import _apply
from ai_trading_lab.live.risk import RiskState
from ai_trading_lab.live.store import LoopState, PlannedDecision
from ai_trading_lab.market_data.models import Source
from ai_trading_lab.paper_execution.accounting import PortfolioState, Position
from ai_trading_lab.paper_execution.models import PaperInstrument, SimulationResult
from tests.unit.test_live_logic import D

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
INSTRUMENT = PaperInstrument(
    source=Source.KRAKEN,
    venue="kraken",
    native_id="XXBTZEUR",
    kind="spot",
    base_unit="XBT",
    cash_unit="EUR",
    tick_size=D("0.1"),
    quantity_step=D("0.00000001"),
    cash_step=D("0.00001"),
)


def state(quantity: str | None) -> PortfolioState:
    positions = (
        (Position(instrument=INSTRUMENT, quantity=D(quantity), cost_basis=D("10")),)
        if quantity
        else ()
    )
    cash = D(90) if quantity else D(100)
    return PortfolioState(
        portfolio_id=UUID(int=1),
        cash_unit="EUR",
        cash_step=D("0.00001"),
        initial_cash=D(100),
        cash=cash,
        positions=positions,
    )


def fill(quantity: str) -> SimulationResult:
    return SimulationResult(
        status="FILLED",
        reason="filled",
        quantity=D(quantity),
        notional=D("10"),
        fee=D("0.08"),
        consumed=(),
        executed_at_utc=NOW + timedelta(seconds=1),
    )


def planned(action: str, reason: str) -> PlannedDecision:
    return PlannedDecision(
        decision_id=uuid4(),
        status="planned",
        created=False,
        pair="XBTEUR",
        action=action,
        reason=reason,
    )


def fresh() -> LoopState:
    return LoopState(
        risk=RiskState(peak_equity=D(100), day=date(2026, 1, 10), day_open_equity=D(100))
    )


def test_a_buy_records_entry_once_and_applying_it_again_changes_nothing() -> None:
    once = _apply(fresh(), planned("buy", "b"), fill("0.0001"), state("0.0001"), INSTRUMENT)
    assert once.entered_at["XBTEUR"] == NOW + timedelta(seconds=1)
    assert once.last_entry_at["XBTEUR"] == once.entered_at["XBTEUR"]
    assert _apply(once, planned("buy", "b"), fill("0.0001"), state("0.0001"), INSTRUMENT) == once


@pytest.mark.parametrize(("reason", "stop"), [("stop_loss", True), ("take_profit", False)])
def test_a_full_exit_clears_the_entry_and_only_a_stop_starts_the_cooldown(
    reason: str, stop: bool
) -> None:
    held = _apply(fresh(), planned("buy", "b"), fill("0.0001"), state("0.0001"), INSTRUMENT)
    closed = _apply(held, planned("sell", reason), fill("0.0001"), state(None), INSTRUMENT)
    assert "XBTEUR" not in closed.entered_at
    assert ("XBTEUR" in closed.last_stop_at) is stop


def test_a_partial_exit_keeps_the_original_entry_time_and_starts_no_cooldown() -> None:
    held = _apply(fresh(), planned("buy", "b"), fill("0.0002"), state("0.0002"), INSTRUMENT)
    partial = _apply(
        held, planned("sell", "stop_loss"), fill("0.0001"), state("0.0001"), INSTRUMENT
    )
    assert partial == held


def test_a_fill_of_nothing_changes_nothing() -> None:
    empty = fill("0.0001").model_copy(update={"quantity": Decimal(0), "status": "CANCELLED"})
    assert _apply(fresh(), planned("buy", "b"), empty, state(None), INSTRUMENT) == fresh()
