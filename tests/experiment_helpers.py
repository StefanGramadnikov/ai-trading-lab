"""Synthetic cohorts; no operational paper policy or empirical evidence."""

from uuid import uuid4

from ai_trading_lab.experiments.models import Cell, ExperimentConfig, RiskProfile
from tests.ai_helpers import ai_spec


def profile(name: str = "moderate") -> RiskProfile:
    return RiskProfile.model_validate(
        dict(
            name=name,
            version="NONOPERATIONAL-fixture-v1",
            max_order_fraction="0.1",
            max_position_fraction="0.2",
            max_exposure_fraction="0.4",
            daily_loss_fraction="0.05",
            drawdown_fraction="0.1",
        )
    )


def experiment() -> ExperimentConfig:
    ai = ai_spec()
    tape = ai.research
    return ExperimentConfig(
        experiment_id=uuid4(),
        version="synthetic-v1",
        dimension="architecture",
        baseline_cell="rules",
        cells=(
            Cell(
                cell_id="rules",
                profile=profile(),
                rules=tape.model_copy(update={"run_id": uuid4()}),
            ),
            Cell(cell_id="hybrid", profile=profile(), ai=ai),
        ),
        minimum_frames=1,
        minimum_fills=0,
        reporting_currency="USD",
        expenses_complete=True,
    )
