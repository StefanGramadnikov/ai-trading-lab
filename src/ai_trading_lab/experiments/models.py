"""Immutable cohorts with explicit controlled differences and paper-only profiles."""

from __future__ import annotations

from decimal import localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.experiments.costs import Expense, FXRate
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.models import PositiveAmount

Fraction = Annotated[PositiveAmount, Field(le=1)]
State = Literal["CREATED", "RUNNING", "EVALUATING", "PAUSED", "APPROVED", "REJECTED"]


class RiskProfile(FrozenModel):
    name: Literal["conservative", "moderate", "aggressive"]
    version: str = Field(min_length=1, max_length=100)
    max_order_fraction: Fraction
    max_position_fraction: Fraction
    max_exposure_fraction: Fraction
    daily_loss_fraction: Fraction
    drawdown_fraction: Fraction

    @model_validator(mode="after")
    def ordered(self) -> RiskProfile:
        if not self.max_order_fraction <= self.max_position_fraction <= self.max_exposure_fraction:
            raise ValueError("profile allocation order")
        return self

    def effective(self, research: ResearchInput) -> ResearchInput:
        # Fractions use opening virtual cash. Never widen an existing M3 limit.
        with localcontext() as ctx:
            ctx.prec = 100
            policy = research.simulation.model_copy(
                update={
                    "max_order_notional": min(
                        research.simulation.max_order_notional,
                        research.initial_cash * self.max_order_fraction,
                    ),
                    "max_position_notional": min(
                        research.simulation.max_position_notional,
                        research.initial_cash * self.max_position_fraction,
                    ),
                    "max_total_exposure": min(
                        research.simulation.max_total_exposure,
                        research.initial_cash * self.max_exposure_fraction,
                    ),
                }
            )
            effective = research.model_copy(
                update={
                    "simulation": policy,
                    "order_notional": min(research.order_notional, policy.max_order_notional),
                }
            )
        return ResearchInput.model_validate(effective.model_dump(mode="json"))


class Cell(FrozenModel):
    cell_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    profile: RiskProfile
    rules: ResearchInput | None = None
    ai: AIRunInput | None = None

    @model_validator(mode="after")
    def mode(self) -> Cell:
        if (self.rules is None) == (self.ai is None):
            raise ValueError("exactly one rules or AI input required")
        if self.ai is not None and (
            self.ai.inference_mode != "fixture" or self.ai.purpose != "development"
        ):
            raise ValueError("M6 permits fixture development inference only")
        return self

    @property
    def research(self) -> ResearchInput:
        if self.rules is not None:
            return self.rules
        assert self.ai is not None
        return self.ai.research


class ExperimentConfig(FrozenModel):
    schema_version: Literal["1"] = "1"
    experiment_id: UUID
    version: str = Field(min_length=1, max_length=100)
    dimension: Literal["architecture", "activation", "provider", "risk"]
    baseline_cell: str
    cells: tuple[Cell, ...] = Field(min_length=2, max_length=16)
    minimum_frames: int = Field(ge=1, le=100, strict=True)
    minimum_fills: int = Field(ge=0, le=10000, strict=True)
    reporting_currency: str = Field(min_length=1, max_length=200)
    expenses: tuple[Expense, ...] = Field(default=(), max_length=1000)
    fx_rates: tuple[FXRate, ...] = Field(default=(), max_length=1000)
    expenses_complete: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def paired(self) -> ExperimentConfig:
        ids = [c.cell_id for c in self.cells]
        if len(set(ids)) != len(ids) or self.baseline_cell not in ids:
            raise ValueError("unique cells and existing baseline required")
        if len({c.research.run_id for c in self.cells}) != len(self.cells):
            raise ValueError("cell run IDs must be independent")
        if (
            len(self.expenses)
            + sum(
                min(c.ai.budget.max_calls, len(c.ai.fixture_results) + 1)
                for c in self.cells
                if c.ai
            )
            > 1000
        ):
            raise ValueError("bounded experiment cost history exceeded")
        for expense in self.expenses:
            if expense.category == "ai" and (
                expense.kind != "actual"
                or not expense.replaces_expense_id
                or not expense.replaces_expense_id.startswith("ai:")
            ):
                raise ValueError("AI invoices must reference authoritative call estimates")
        first = self.cells[0]
        common = first.research.model_dump(mode="json", exclude={"run_id"})
        ai_signatures: list[dict[str, object]] = []
        for cell in self.cells:
            if cell.research.model_dump(mode="json", exclude={"run_id"}) != common:
                raise ValueError("paired research tape/window/capital/strategy mismatch")
            if self.dimension != "risk" and cell.profile != first.profile:
                raise ValueError("undeclared risk difference")
            if self.dimension != "architecture" and (cell.ai is None) != (first.ai is None):
                raise ValueError("undeclared architecture difference")
            if cell.ai is not None:
                signature = cell.ai.model_dump(mode="json", exclude={"research", "fixture_results"})
                budget = signature["budget"]
                assert isinstance(budget, dict)
                budget.pop("budget_id")
                if self.dimension == "architecture":
                    signature.pop("flow")
                elif self.dimension == "activation":
                    signature.pop("activation")
                    signature.pop("interval_seconds")
                elif self.dimension == "provider":
                    for key in ("provider", "model", "price"):
                        signature.pop(key)
                ai_signatures.append(signature)
        if any(s != ai_signatures[0] for s in ai_signatures[1:]):
            raise ValueError("undeclared AI configuration difference")
        return self

    @property
    def fingerprint(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def transition(state: State, action: str, resume_state: State | None = None) -> State:
    allowed: dict[tuple[str, str], State] = {
        ("CREATED", "start"): "RUNNING",
        ("RUNNING", "finish"): "EVALUATING",
        ("CREATED", "pause"): "PAUSED",
        ("RUNNING", "pause"): "PAUSED",
        ("EVALUATING", "pause"): "PAUSED",
        ("EVALUATING", "approve"): "APPROVED",
        ("EVALUATING", "reject"): "REJECTED",
    }
    if (
        state == "PAUSED"
        and action == "resume"
        and resume_state in ("CREATED", "RUNNING", "EVALUATING")
    ):
        return resume_state
    if (state, action) not in allowed:
        raise ValueError("invalid experiment transition")
    return allowed[state, action]
