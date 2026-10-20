"""Paired inputs may differ only along the declared comparison dimension."""

from decimal import Decimal, localcontext
from typing import cast

import pytest

from ai_trading_lab.experiments.models import ExperimentConfig, RiskProfile, State, transition
from tests.experiment_helpers import experiment, profile


def test_pair_and_effective_limits_are_exact_and_conservative() -> None:
    spec = experiment()
    tape = spec.cells[0].research
    with localcontext() as ctx:
        ctx.prec = 3
        effective = profile().effective(tape)
    assert effective.simulation.max_order_notional == Decimal(10)
    assert effective.simulation.max_position_notional == Decimal(20)
    assert effective.simulation.max_total_exposure == Decimal(40)
    assert tape.simulation.max_order_notional == Decimal(1000)
    assert spec.fingerprint == ExperimentConfig.model_validate(spec.model_dump()).fingerprint


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_cash", "200"),
        ("cash_unit", "EUR"),
        ("order_notional", "11"),
        ("order_ttl_seconds", 40),
        ("code_version", "f" * 40),
    ],
)
def test_unmatched_research_is_rejected(field: str, value: object) -> None:
    spec = experiment().model_dump(mode="json")
    spec["cells"][1]["ai"]["research"][field] = value
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(spec)


@pytest.mark.parametrize("change", ["risk", "duplicate_cell", "run_id", "smoke", "real"])
def test_invalid_cell_and_undeclared_changes(change: str) -> None:
    spec = experiment().model_dump(mode="json")
    if change == "risk":
        spec["cells"][1]["profile"]["drawdown_fraction"] = "0.2"
    elif change == "duplicate_cell":
        spec["cells"][1]["cell_id"] = "rules"
    elif change == "run_id":
        spec["cells"][1]["ai"]["research"]["run_id"] = spec["cells"][0]["rules"]["run_id"]
    elif change == "smoke":
        spec["cells"][1]["ai"]["purpose"] = "integration_smoke"
    else:
        spec["cells"][1]["ai"]["inference_mode"] = "real"
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(spec)


@pytest.mark.parametrize("name", ["conservative", "moderate", "aggressive"])
def test_explicit_named_profiles(name: str) -> None:
    assert profile(name).name == name


@pytest.mark.parametrize(
    "update",
    [
        {"max_order_fraction": "1.1"},
        {"max_order_fraction": "0"},
        {"drawdown_fraction": "NaN"},
        {"max_order_fraction": "0.9"},
    ],
)
def test_invalid_profile(update: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RiskProfile.model_validate(profile().model_dump() | update)


@pytest.mark.parametrize(
    "state,action,target",
    [
        ("CREATED", "start", "RUNNING"),
        ("RUNNING", "finish", "EVALUATING"),
        ("RUNNING", "pause", "PAUSED"),
        ("EVALUATING", "approve", "APPROVED"),
        ("EVALUATING", "reject", "REJECTED"),
    ],
)
def test_lifecycle(state: str, action: str, target: str) -> None:
    assert transition(cast(State, state), action) == target


def test_resume_and_terminal_refusals() -> None:
    assert transition("PAUSED", "resume", "RUNNING") == "RUNNING"
    for state in ("CREATED", "RUNNING", "APPROVED", "REJECTED"):
        with pytest.raises(ValueError):
            transition(state, "approve")


def test_declared_risk_and_architecture_dimensions() -> None:
    from uuid import uuid4

    from ai_trading_lab.experiments.models import Cell

    spec = experiment()
    tape = spec.cells[0].research
    cells = tuple(
        Cell(cell_id=name, profile=profile(name), rules=tape.model_copy(update={"run_id": uuid4()}))
        for name in ("conservative", "moderate", "aggressive")
    )
    obj = ExperimentConfig.model_validate(
        spec.model_dump() | {"dimension": "risk", "cells": cells, "baseline_cell": "moderate"}
    )
    assert len(obj.cells) == 3


@pytest.mark.parametrize(
    "dimension,field,value",
    [
        ("activation", "activation", "periodic"),
        ("provider", "model", "fixture-alternative"),
    ],
)
def test_declared_ai_differences_and_undeclared_rejection(
    dimension: str, field: str, value: str
) -> None:
    from uuid import uuid4

    from ai_trading_lab.experiments.models import Cell

    spec = experiment()
    ai = spec.cells[1].ai
    assert ai is not None
    alt = ai.model_copy(
        update={"research": ai.research.model_copy(update={"run_id": uuid4()}), field: value}
    )
    if field == "model":
        alt = alt.model_copy(update={"price": alt.price.model_copy(update={"model": value})})
    cells = (spec.cells[1], Cell(cell_id="alternative", profile=profile(), ai=alt))
    raw = spec.model_dump() | {"dimension": dimension, "cells": cells, "baseline_cell": "hybrid"}
    assert ExperimentConfig.model_validate(raw).dimension == dimension
    with pytest.raises(ValueError, match="undeclared"):
        ExperimentConfig.model_validate(raw | {"dimension": "risk"})


def test_pair_rejects_snapshot_and_window_differences() -> None:
    from datetime import timedelta

    spec = experiment()
    raw = spec.model_dump()
    ai = spec.cells[1].ai
    assert ai is not None
    changed = ai.model_copy(
        update={
            "research": ai.research.model_copy(
                update={"end_at_utc": ai.research.end_at_utc + timedelta(seconds=1)}
            )
        }
    )
    raw["cells"] = (spec.cells[0], spec.cells[1].model_copy(update={"ai": changed}))
    with pytest.raises(ValueError, match="paired"):
        ExperimentConfig.model_validate(raw)
