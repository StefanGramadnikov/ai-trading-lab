"""Profile entry suspension never prevents a qualified deterministic exit."""

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.experiments.metrics import MetricPoint
from ai_trading_lab.experiments.runner import evaluate_cell, suspension
from ai_trading_lab.strategies.models import StrategyFrame
from tests.experiment_helpers import experiment, profile
from tests.integration.test_paper_store import NOW


def point(nav: str | None = "100", realized: str = "0", fees: str = "0") -> MetricPoint:
    return MetricPoint(
        at_utc=NOW,
        cash_unit="USD",
        nav=nav,
        cash="80",
        realized_gross=realized,
        fees=fees,
        exposure_cost_basis="20",
        open_positions=1,
        marks_complete=nav is not None,
    )


@pytest.mark.parametrize(
    "nav,realized,fees,reason",
    [
        (None, "0", "0", "unavailable_nav"),
        ("90", "0", "0", "drawdown_limit"),
        ("90.01", "0", "0", None),
        ("100", "-5", "0", "daily_loss_limit"),
        ("100", "-4.99", "0.01", "daily_loss_limit"),
        ("100", "-4.99", "0", None),
    ],
)
def test_loss_threshold_equality_and_fee_basis(
    nav: str | None, realized: str, fees: str, reason: str | None
) -> None:
    assert (
        suspension(profile(), Decimal(100), Decimal(100), Decimal(0), point(nav, realized, fees))
        == reason
    )


def tape_with_exit() -> tuple[StrategyFrame, ...]:
    first = experiment().cells[0].research.frames[0]
    assert first.forecast is not None
    out = [first]
    for index, (bid, probability) in enumerate((("0.40", "0.1"), ("0.30", "0.8")), 1):
        at = NOW + timedelta(seconds=index)
        quote = first.quote.model_copy(
            update={
                "snapshot_id": uuid4(),
                "event_time_utc": at,
                "received_at_utc": at,
                "bids": (first.quote.bids[0].model_copy(update={"price": Decimal(bid)}),),
            }
        )
        forecast = first.forecast.model_copy(
            update={
                "probability": Decimal(probability),
                "produced_at_utc": at,
                "available_at_utc": at,
            }
        )
        out.append(first.model_copy(update={"at_utc": at, "quote": quote, "forecast": forecast}))
    return tuple(out)


@pytest.mark.asyncio
async def test_drawdown_latches_entries_but_exit_continues() -> None:
    spec = experiment()
    frames = tape_with_exit()
    cell = spec.cells[0]
    research = cell.research.model_copy(
        update={"frames": frames, "end_at_utc": NOW + timedelta(seconds=3)}
    )
    cell = cell.model_copy(
        update={
            "rules": research,
            "profile": cell.profile.model_copy(update={"drawdown_fraction": Decimal("0.01")}),
        }
    )
    result = await evaluate_cell(spec, cell, cast(AsyncEngine, None))
    output: Any = result.report
    assert output["entry_suspended"] is True
    assert result.report["suspension_reason"] == "drawdown_limit"
    assert len(result.history.state.positions) == 0
    assert output["counts"]["fills"] == 2
    assert output["decisions"][2]["decision"]["reason"] == "drawdown_limit"


@pytest.mark.asyncio
async def test_unmarked_exposure_suspends_without_invented_profit() -> None:
    spec = experiment()
    cell = spec.cells[0]
    frame = cell.research.frames[0]
    at = frame.at_utc + timedelta(seconds=1)
    quote = frame.quote.model_copy(
        update={"bids": (), "snapshot_id": uuid4(), "event_time_utc": at, "received_at_utc": at}
    )
    research = cell.research.model_copy(
        update={"frames": (frame, frame.model_copy(update={"quote": quote, "at_utc": at}))}
    )
    result = await evaluate_cell(
        spec, cell.model_copy(update={"rules": research}), cast(AsyncEngine, None)
    )
    output: Any = result.report
    assert output["entry_suspended"] is True
    assert output["metrics"]["trading_net_pnl"] is None
    assert result.report["nav"] is None


@pytest.mark.asyncio
async def test_declared_endpoint_valuation_cannot_use_earlier_mark() -> None:
    spec = experiment()
    cell = spec.cells[0]
    research = cell.research.model_copy(update={"end_at_utc": NOW + timedelta(days=1)})
    result = await evaluate_cell(
        spec, cell.model_copy(update={"rules": research}), cast(AsyncEngine, None)
    )
    output: Any = result.report
    assert output["nav"] is None and output["metrics"]["nav"] is None
    assert output["metrics"]["trading_net_pnl"] is None
    assert output["equity_curve"][-1]["at_utc"] == research.end_at_utc.isoformat().replace(
        "+00:00", "Z"
    )
    assert output["entry_suspended"] is True
    assert output["suspension_reason"] == "unavailable_nav"


@pytest.mark.asyncio
@pytest.mark.parametrize("crosses_utc_day", [False, True])
async def test_daily_realized_loss_uses_utc_opening_balance(crosses_utc_day: bool) -> None:
    spec = experiment()
    cell = spec.cells[0]
    original = cell.research.frames[0]
    assert original.forecast is not None
    frames = []
    offset = 86400 if crosses_utc_day else 2
    for seconds, bid, probability in (
        (0, "0.49", "0.8"),
        (1, "0.40", "0.1"),
        (offset, "0.49", "0.8"),
        (offset + 1, "0.30", "0.1"),
    ):
        at = NOW + timedelta(seconds=seconds)
        quote = original.quote.model_copy(
            update={
                "snapshot_id": uuid4(),
                "event_time_utc": at,
                "received_at_utc": at,
                "bids": (original.quote.bids[0].model_copy(update={"price": Decimal(bid)}),),
            }
        )
        forecast = original.forecast.model_copy(
            update={
                "probability": Decimal(probability),
                "produced_at_utc": at,
                "available_at_utc": at,
            }
        )
        frames.append(
            original.model_copy(update={"quote": quote, "forecast": forecast, "at_utc": at})
        )
    research = cell.research.model_copy(
        update={"frames": tuple(frames), "end_at_utc": frames[-1].at_utc + timedelta(seconds=1)}
    )
    evaluated = await evaluate_cell(
        spec, cell.model_copy(update={"rules": research}), cast(AsyncEngine, None)
    )
    assert evaluated.history.state.realized_gross == Decimal("-6")
    assert evaluated.report["entry_suspended"] is (not crosses_utc_day)
    assert evaluated.report["suspension_reason"] == (
        None if crosses_utc_day else "daily_loss_limit"
    )
    assert not evaluated.history.state.positions


@pytest.mark.asyncio
@pytest.mark.parametrize("same_execution_day", [False, True])
async def test_latency_midnight_loss_belongs_to_execution_day(same_execution_day: bool) -> None:
    from datetime import datetime

    spec = experiment()
    cell = spec.cells[0]
    original = cell.research.frames[0]
    assert original.forecast is not None
    start = NOW.replace(hour=23, minute=59, second=56)
    first_sell_seconds = 3.5 if same_execution_day else 2.5
    frames = []
    for seconds, bid, probability in (
        (0, "0.49", "0.8"),
        (first_sell_seconds, "0.40", "0.1"),
        (6, "0.49", "0.8"),
        (8, "0.30", "0.1"),
    ):
        at = start + timedelta(seconds=seconds)
        quote = original.quote.model_copy(
            update={
                "snapshot_id": uuid4(),
                "event_time_utc": at,
                "received_at_utc": at,
                "bids": (original.quote.bids[0].model_copy(update={"price": Decimal(bid)}),),
            }
        )
        forecast = original.forecast.model_copy(
            update={
                "probability": Decimal(probability),
                "produced_at_utc": at,
                "available_at_utc": at,
            }
        )
        frames.append(
            original.model_copy(update={"quote": quote, "forecast": forecast, "at_utc": at})
        )
    research = cell.research.model_copy(
        update={
            "frames": tuple(frames),
            "start_at_utc": start,
            "end_at_utc": frames[-1].at_utc + timedelta(seconds=2),
            "simulation": cell.research.simulation.model_copy(update={"latency_ms": 1000}),
        }
    )
    evaluated = await evaluate_cell(
        spec, cell.model_copy(update={"rules": research}), cast(AsyncEngine, None)
    )
    output: Any = evaluated.report
    first_day = datetime.fromisoformat(
        output["decisions"][1]["receipt"]["result"]["executed_at_utc"]
    ).date()
    second_day = datetime.fromisoformat(
        output["decisions"][3]["receipt"]["result"]["executed_at_utc"]
    ).date()
    assert (first_day == second_day) is same_execution_day
    assert evaluated.history.state.realized_gross == Decimal("-6")
    assert output["entry_suspended"] is same_execution_day
    assert output["suspension_reason"] == ("daily_loss_limit" if same_execution_day else None)
    assert not evaluated.history.state.positions
