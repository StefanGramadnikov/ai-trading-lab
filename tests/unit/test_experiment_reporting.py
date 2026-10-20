"""Economic comparisons conserve shared costs and keep missing evidence visible."""

from decimal import Decimal, localcontext
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.experiments.costs import CostAllocation, Expense, FXRate
from ai_trading_lab.experiments.models import Cell
from ai_trading_lab.experiments.reporting import build_report
from ai_trading_lab.experiments.runner import evaluate_cell
from tests.experiment_helpers import experiment, profile


async def results() -> tuple[Any, tuple[dict[str, Any], ...]]:
    spec = experiment()
    tape = spec.cells[0].research
    spec = spec.model_copy(
        update={
            "cells": (
                spec.cells[0],
                Cell(
                    cell_id="control",
                    profile=profile(),
                    rules=tape.model_copy(update={"run_id": uuid4()}),
                ),
            )
        }
    )
    reports = tuple(
        [(await evaluate_cell(spec, cell, cast(AsyncEngine, None))).report for cell in spec.cells]
    )
    return spec, reports


@pytest.mark.asyncio
async def test_shared_costs_subtracted_once_and_pair_deltas() -> None:
    spec, reports = await results()
    at = spec.cells[0].research.end_at_utc
    expense = Expense(
        expense_id="host",
        cost_key="host-month",
        category="hosting",
        kind="actual",
        amount="2",
        currency="USD",
        incurred_at_utc=at,
        source="synthetic-invoice",
        evidence="synthetic",
        allocations=(
            CostAllocation(cell_id="rules", weight="0.5"),
            CostAllocation(cell_id="control", weight="0.5"),
        ),
    )
    spec = spec.model_copy(update={"expenses": (expense,)})
    with localcontext() as ctx:
        ctx.prec = 3
        out: Any = build_report(spec, reports)
    assert out["evidence_sufficient"] is True
    assert out["costs"]["consolidated"]["actual_cost"] == "2"
    assert out["costs"]["per_cell"][0]["actual_cost"] == "1.0"
    assert out["paired_comparisons"][0]["economic_net_delta"] == "0.00"
    trading = sum((Decimal(r["metrics"]["trading_net_pnl"]) for r in reports), Decimal(0))
    assert Decimal(out["consolidated_economic_net_pnl"]) == trading - 2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["undeclared", "currency", "missing_cell", "unknown_cost"])
async def test_unavailable_economic_evidence(case: str) -> None:
    spec, reports = await results()
    if case == "undeclared":
        spec = spec.model_copy(update={"expenses_complete": False})
    elif case == "currency":
        spec = spec.model_copy(update={"reporting_currency": "EUR"})
    elif case == "missing_cell":
        reports = reports[:1]
    else:
        expense = Expense(
            expense_id="host",
            cost_key="host-month",
            category="hosting",
            kind="actual",
            amount=None,
            currency="USD",
            incurred_at_utc=spec.cells[0].research.end_at_utc,
            source="invoice-pending",
            evidence="synthetic",
            unavailable_reason="unbilled",
            allocations=(CostAllocation(cell_id="rules", weight="1"),),
        )
        spec = spec.model_copy(update={"expenses": (expense,)})
    out: Any = build_report(spec, reports)
    assert out["consolidated_economic_net_pnl"] is None
    assert out["evidence_sufficient"] is False
    assert out["paired_comparisons"][0]["economic_net_delta"] is None
    assert out["cells"][0]["unavailable_reasons"] or case == "missing_cell"


@pytest.mark.asyncio
async def test_explicit_profit_fx_and_missing_quote_does_not_become_usd() -> None:
    spec, reports = await results()
    at = spec.cells[0].research.end_at_utc
    rate = FXRate(
        base_currency="USD",
        quote_currency="EUR",
        rate="0.9",
        date=at.date(),
        observed_at_utc=at,
        source="synthetic-rate",
        evidence="synthetic",
    )
    out: Any = build_report(
        spec.model_copy(update={"reporting_currency": "EUR", "fx_rates": (rate,)}), reports
    )
    assert out["evidence_sufficient"] is True
    assert Decimal(out["cells"][0]["economic_net_pnl"]) == Decimal(
        reports[0]["metrics"]["trading_net_pnl"]
    ) * Decimal("0.9")


@pytest.mark.asyncio
async def test_report_identity_and_duplicates_rejected() -> None:
    spec, reports = await results()
    with pytest.raises(ValueError, match="duplicate"):
        build_report(spec, (reports[0], reports[0]))
    bad = dict(reports[0], engine_hash="0" * 64)
    with pytest.raises(ValueError, match="runtime"):
        build_report(spec, (bad, reports[1]))
    with pytest.raises(ValueError, match="unknown"):
        build_report(spec, (dict(reports[0], cell_id="absent"),))


@pytest.mark.asyncio
async def test_public_curves_and_endpoint_gap_are_preserved() -> None:
    from datetime import timedelta

    spec, _ = await results()
    cells = tuple(
        c.model_copy(
            update={
                "rules": c.research.model_copy(
                    update={"end_at_utc": c.research.end_at_utc + timedelta(days=1)}
                )
            }
        )
        for c in spec.cells
    )
    spec = spec.model_copy(update={"cells": cells})
    reports = tuple([(await evaluate_cell(spec, c, cast(AsyncEngine, None))).report for c in cells])
    out: Any = build_report(spec, reports)
    assert out["evidence_sufficient"] is False
    assert out["consolidated_economic_net_pnl"] is None
    assert out["cells"][0]["equity_curve"] == reports[0]["equity_curve"]
    assert out["cells"][0]["equity_curve"][-1]["nav"] is None
