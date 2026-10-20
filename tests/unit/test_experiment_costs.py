from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from ai_trading_lab.experiments.costs import (
    CostAllocation,
    Expense,
    FXRate,
    allocate_costs,
)

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def expense(**changes: object) -> Expense:
    return Expense.model_validate(
        {
            "expense_id": "invoice-1",
            "cost_key": "hosting-october",
            "category": "hosting",
            "kind": "actual",
            "amount": "10",
            "currency": "USD",
            "incurred_at_utc": NOW,
            "source": "invoice/document/1",
            "evidence": "observed",
            "allocations": [{"cell_id": "rules", "weight": "1"}],
            **changes,
        }
    )


def fx(**changes: object) -> FXRate:
    return FXRate.model_validate(
        {
            "base_currency": "USD",
            "quote_currency": "EUR",
            "rate": "0.9",
            "date": date(2026, 10, 20),
            "observed_at_utc": NOW,
            "source": "dated-fixture",
            "evidence": "synthetic",
            **changes,
        }
    )


def test_direct_and_shared_costs_are_counted_once() -> None:
    shared = expense(
        allocations=[
            {"cell_id": "rules", "weight": "0.25"},
            {"cell_id": "hybrid", "weight": "0.75"},
        ]
    )
    direct = expense(
        expense_id="call-1",
        cost_key="call-1",
        category="ai",
        kind="estimated",
        amount="0.5",
        evidence="synthetic",
        allocations=[{"cell_id": "hybrid", "weight": "1"}],
    )
    report = allocate_costs([shared, direct], ["rules", "hybrid"], "EUR", [fx()])
    assert report.consolidated.actual_cost == Decimal("9")
    assert report.consolidated.estimated_cost == Decimal("0.45")
    assert report.consolidated.total_cost == Decimal("9.45")
    assert [cell.total_cost for cell in report.per_cell] == [Decimal("2.25"), Decimal("7.2")]
    assert report.consolidated.evidence == ("observed", "synthetic")
    assert report.model_dump(mode="json")["consolidated"]["actual_cost"] == "9.0"


def test_invoice_replaces_estimate_without_double_counting() -> None:
    estimate = expense(expense_id="estimate", kind="estimated", amount="20")
    invoice = expense(amount="12", replaces_expense_id="estimate")
    report = allocate_costs([invoice, estimate], ["rules"], "USD")
    assert report.consolidated.total_cost == 12
    assert report.consolidated.estimated_cost == 0
    assert report.excluded_estimate_ids == ("estimate",)
    assert report.included_expense_ids == ("invoice-1",)
    assert len(report.expenses) == 2


def test_unknown_billing_is_not_zero_and_only_taints_affected_cells() -> None:
    pending = expense(kind="estimated", amount=None, unavailable_reason="billing unknown")
    report = allocate_costs([pending], ["rules", "hybrid"], "USD")
    assert report.consolidated.estimated_cost is None
    assert report.consolidated.actual_cost == 0
    assert report.consolidated.total_cost is None
    assert report.per_cell[0].complete is False
    assert "billing unknown" in report.per_cell[0].unavailable_reasons[0]
    assert report.per_cell[1].complete is True
    assert report.per_cell[1].total_cost == 0


@pytest.mark.parametrize(
    "rates",
    [[], [fx(date=date(2026, 10, 19))], [fx(base_currency="EUR", quote_currency="USD")]],
)
def test_fx_must_be_explicit_direct_and_dated(rates: list[FXRate]) -> None:
    report = allocate_costs([expense()], ["rules"], "EUR", rates)
    assert report.consolidated.actual_cost is None
    assert not report.consolidated.complete
    assert "missing dated FX USD->EUR" in report.consolidated.unavailable_reasons[0]


def test_generic_collateral_is_not_usd() -> None:
    report = allocate_costs([expense(currency="COLLATERAL")], ["rules"], "USD")
    assert report.consolidated.total_cost is None


def test_synthetic_fx_cannot_appear_as_pure_observed_cost() -> None:
    report = allocate_costs([expense()], ["rules"], "EUR", [fx()])
    assert report.consolidated.evidence == ("observed", "synthetic")
    assert report.per_cell[0].evidence == ("observed", "synthetic")


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "-1"},
        {"amount": "NaN"},
        {"amount": 0.1},
        {"amount": True},
        {"amount": None},
        {"unavailable_reason": "unknown"},
        {"incurred_at_utc": datetime(2026, 10, 20)},
        {"currency": "usd"},
        {"source": ""},
        {"allocations": []},
        {"allocations": [{"cell_id": "rules", "weight": "0.5"}]},
        {"allocations": [{"cell_id": "rules", "weight": 1.0}]},
        {
            "allocations": [
                {"cell_id": "rules", "weight": "0.5"},
                {"cell_id": "rules", "weight": "0.5"},
            ]
        },
        {"kind": "estimated", "replaces_expense_id": "old"},
    ],
)
def test_expenses_reject_invalid_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        expense(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"rate": "0"},
        {"rate": "Infinity"},
        {"rate": 0.9},
        {"base_currency": "EUR"},
        {"observed_at_utc": datetime(2026, 10, 20)},
    ],
)
def test_fx_rejects_invalid_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        fx(**changes)


@pytest.mark.parametrize("cells", [[], ["rules", "rules"], ["other"], ["rules"] * 101])
def test_bad_cell_identity_is_rejected(cells: list[str]) -> None:
    with pytest.raises(ValueError):
        allocate_costs([expense()], cells, "USD")


def test_duplicate_cost_identity_requires_linked_replacement() -> None:
    with pytest.raises(ValueError, match="Duplicate expense"):
        allocate_costs([expense(), expense()], ["rules"], "USD")
    with pytest.raises(ValueError, match="Duplicate economic"):
        allocate_costs([expense(), expense(expense_id="other")], ["rules"], "USD")


@pytest.mark.parametrize(
    "changes",
    [
        {"replaces_expense_id": "missing"},
        {"cost_key": "other"},
        {"category": "ai"},
        {"evidence": "synthetic"},
        {"allocations": [{"cell_id": "hybrid", "weight": "1"}]},
    ],
)
def test_invoice_cannot_relabel_or_replace_unrelated_cost(changes: dict[str, object]) -> None:
    estimate = expense(expense_id="estimate", kind="estimated")
    invoice = (
        expense(replaces_expense_id="estimate", **changes)
        if "replaces_expense_id" not in changes
        else expense(**changes)
    )
    with pytest.raises(ValueError, match="matching estimate"):
        allocate_costs([estimate, invoice], ["rules", "hybrid"], "USD")


def test_two_invoices_cannot_replace_same_estimate() -> None:
    costs = [
        expense(expense_id="estimate", kind="estimated"),
        expense(replaces_expense_id="estimate"),
        expense(expense_id="invoice-2", replaces_expense_id="estimate"),
    ]
    with pytest.raises(ValueError, match="matching estimate"):
        allocate_costs(costs, ["rules"], "USD")


def test_ambiguous_fx_and_oversized_evidence_are_rejected() -> None:
    with pytest.raises(ValueError, match="Ambiguous FX"):
        allocate_costs([], ["rules"], "EUR", [fx(), fx()])
    with pytest.raises(ValueError, match="bounded"):
        allocate_costs([expense()] * 1001, ["rules"], "USD")
    with pytest.raises(ValueError, match="bounded"):
        allocate_costs([], ["rules"], "EUR", [fx()] * 1001)


def test_allocation_is_exact_under_host_decimal_context() -> None:
    cost = expense(
        amount="123456789.123456789123456789",
        allocations=[
            {"cell_id": "rules", "weight": "0.333333333333333333"},
            {"cell_id": "hybrid", "weight": "0.666666666666666667"},
        ],
    )
    with localcontext() as context:
        context.prec = 3
        report = allocate_costs([cost], ["rules", "hybrid"], "EUR", [fx()])
    with localcontext() as context:
        context.prec = 160
        assert sum((cell.total_cost or Decimal(0) for cell in report.per_cell), Decimal(0)) == (
            report.consolidated.total_cost
        )
        assert cost.amount is not None
        assert report.consolidated.total_cost == cost.amount * Decimal("0.9")


def test_empty_expenses_are_declared_zero() -> None:
    report = allocate_costs([], ["rules"], "USD")
    assert report.consolidated.complete
    assert report.consolidated.total_cost == 0
    assert report.consolidated.evidence == ()


def test_model_copy_cannot_bypass_accounting_validation() -> None:
    invalid = expense().model_copy(update={"amount": Decimal(-1)})
    with pytest.raises(ValidationError):
        allocate_costs([invalid], ["rules"], "USD")
    with pytest.raises(ValidationError):
        CostAllocation(cell_id="rules", weight=Decimal("NaN"))
