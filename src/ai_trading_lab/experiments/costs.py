"""Immutable operating-expense evidence and exact, once-only allocation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.market_data.models import FrozenModel

Identifier = Annotated[str, Field(min_length=1, max_length=160)]
Currency = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_:-]{0,63}$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=40, decimal_places=18)]
Evidence = Literal["synthetic", "observed"]


class CostAllocation(FrozenModel):
    cell_id: Identifier
    weight: Decimal = Field(gt=0, le=1, max_digits=20, decimal_places=18)

    @field_validator("weight", mode="before")
    @classmethod
    def exact_weight(cls, value: object) -> object:
        if isinstance(value, (float, bool)):
            raise ValueError("Weights require exact decimal inputs")
        return value


class Expense(FrozenModel):
    expense_id: Identifier
    cost_key: Identifier
    category: Literal["ai", "data", "hosting", "other"]
    kind: Literal["estimated", "actual"]
    amount: Amount | None
    currency: Currency
    incurred_at_utc: AwareDatetime
    source: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: Evidence
    allocations: tuple[CostAllocation, ...] = Field(min_length=1, max_length=100)
    replaces_expense_id: Identifier | None = None
    unavailable_reason: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def exact_amount(cls, value: object) -> object:
        if isinstance(value, (float, bool)):
            raise ValueError("Costs require exact decimal inputs")
        return value

    @field_validator("incurred_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coherent(self) -> Expense:
        if (self.amount is None) != (self.unavailable_reason is not None):
            raise ValueError("Unknown amount requires a reason; known amount must omit it")
        if self.replaces_expense_id and self.kind != "actual":
            raise ValueError("Only invoices may replace estimates")
        if len({part.cell_id for part in self.allocations}) != len(self.allocations):
            raise ValueError("Duplicate allocation cell")
        with localcontext() as context:
            context.prec = 160
            if sum((part.weight for part in self.allocations), Decimal(0)) != 1:
                raise ValueError("Explicit allocation weights must sum exactly to one")
        return self


class FXRate(FrozenModel):
    base_currency: Currency
    quote_currency: Currency
    rate: Decimal = Field(gt=0, max_digits=40, decimal_places=18)
    date: date
    observed_at_utc: AwareDatetime
    source: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: Evidence

    @field_validator("rate", mode="before")
    @classmethod
    def exact_rate(cls, value: object) -> object:
        if isinstance(value, (float, bool)):
            raise ValueError("FX requires exact decimal inputs")
        return value

    @field_validator("observed_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def distinct_units(self) -> FXRate:
        if self.base_currency == self.quote_currency:
            raise ValueError("Same-unit conversion must not supply an FX rate")
        return self


class CostTotals(FrozenModel):
    estimated_cost: Decimal | None
    actual_cost: Decimal | None
    total_cost: Decimal | None
    complete: bool
    unavailable_reasons: tuple[str, ...]
    evidence: tuple[Evidence, ...]


class CellCost(CostTotals):
    cell_id: Identifier


class CostReport(FrozenModel):
    reporting_currency: Currency
    per_cell: tuple[CellCost, ...]
    consolidated: CostTotals
    included_expense_ids: tuple[str, ...]
    excluded_estimate_ids: tuple[str, ...]
    expenses: tuple[Expense, ...]
    fx_rates: tuple[FXRate, ...]


def _totals(
    entries: list[tuple[Expense, Decimal | None, str | None]],
    fx_evidence: set[Evidence],
) -> CostTotals:
    reasons = tuple(sorted({reason for _, _, reason in entries if reason is not None}))
    estimated: Decimal | None = Decimal(0)
    actual: Decimal | None = Decimal(0)
    for expense, amount, _ in entries:
        if expense.kind == "estimated":
            estimated = None if amount is None or estimated is None else estimated + amount
        else:
            actual = None if amount is None or actual is None else actual + amount
    return CostTotals(
        estimated_cost=estimated,
        actual_cost=actual,
        total_cost=None if estimated is None or actual is None else estimated + actual,
        complete=not reasons,
        unavailable_reasons=reasons,
        evidence=tuple(sorted({expense.evidence for expense, _, _ in entries} | fx_evidence)),
    )


def allocate_costs(
    expenses: Sequence[Expense],
    cell_ids: Sequence[str],
    reporting_currency: str,
    fx_rates: Sequence[FXRate] = (),
) -> CostReport:
    """Allocate each economic cost once, replacing estimates only by linked invoices.

    No inverse rates, date interpolation, unit equivalence or implicit allocation
    is inferred. An empty expense set is a declared zero, not missing evidence.
    """
    if not 1 <= len(cell_ids) <= 100 or len(set(cell_ids)) != len(cell_ids):
        raise ValueError("Require one to 100 unique cells")
    if len(expenses) > 1000 or len(fx_rates) > 1000:
        raise ValueError("Cost input exceeds bounded evidence limit")
    # Revalidate trusted Python callers as well as parsed JSON/model-copy inputs.
    checked = tuple(Expense.model_validate(item.model_dump()) for item in expenses)
    rates = tuple(FXRate.model_validate(item.model_dump()) for item in fx_rates)
    by_id = {item.expense_id: item for item in checked}
    if len(by_id) != len(checked):
        raise ValueError("Duplicate expense identity")
    excluded: set[str] = set()
    for item in checked:
        if any(part.cell_id not in cell_ids for part in item.allocations):
            raise ValueError("Expense allocation references an unknown cell")
        if item.replaces_expense_id is not None:
            previous = by_id.get(item.replaces_expense_id)
            if (
                previous is None
                or previous.kind != "estimated"
                or previous.cost_key != item.cost_key
                or previous.category != item.category
                or previous.evidence != item.evidence
                or dict((p.cell_id, p.weight) for p in previous.allocations)
                != dict((p.cell_id, p.weight) for p in item.allocations)
                or previous.expense_id in excluded
            ):
                raise ValueError("Invoice must replace exactly one matching estimate")
            excluded.add(previous.expense_id)
    effective = tuple(item for item in checked if item.expense_id not in excluded)
    if len({item.cost_key for item in effective}) != len(effective):
        raise ValueError("Duplicate economic cost; link its invoice to its estimate")
    rate_keys = [(rate.base_currency, rate.quote_currency, rate.date) for rate in rates]
    if len(set(rate_keys)) != len(rates):
        raise ValueError("Ambiguous FX evidence")
    rate_by_key = dict(zip(rate_keys, rates, strict=True))
    entries: list[tuple[Expense, Decimal | None, str | None]] = []
    per_cell: dict[str, list[tuple[Expense, Decimal | None, str | None]]] = {
        cell: [] for cell in cell_ids
    }
    converted_evidence: set[Evidence] = set()
    cell_fx: dict[str, set[Evidence]] = {cell: set() for cell in cell_ids}
    # Input numeric precision is bounded; this covers products and all 1000 sums
    # without dependence on a caller's ambient Decimal context or currency rounding.
    with localcontext() as context:
        context.prec = 160
        for item in effective:
            amount = item.amount
            reason = f"{item.expense_id}: {item.unavailable_reason}" if amount is None else None
            if amount is not None and item.currency != reporting_currency:
                rate = rate_by_key.get(
                    (item.currency, reporting_currency, item.incurred_at_utc.date())
                )
                if rate is None:
                    amount = None
                    reason = (
                        f"{item.expense_id}: missing dated FX {item.currency}->{reporting_currency}"
                    )
                else:
                    amount *= rate.rate
                    converted_evidence.add(rate.evidence)
                    for part in item.allocations:
                        cell_fx[part.cell_id].add(rate.evidence)
            entries.append((item, amount, reason))
            for part in item.allocations:
                allocated = None if amount is None else amount * part.weight
                per_cell[part.cell_id].append((item, allocated, reason))
        return CostReport(
            reporting_currency=reporting_currency,
            per_cell=tuple(
                CellCost(cell_id=cell, **_totals(per_cell[cell], cell_fx[cell]).model_dump())
                for cell in cell_ids
            ),
            consolidated=_totals(entries, converted_evidence),
            included_expense_ids=tuple(item.expense_id for item in effective),
            excluded_estimate_ids=tuple(sorted(excluded)),
            expenses=checked,
            fx_rates=rates,
        )
