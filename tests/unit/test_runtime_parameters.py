"""Cadence contradictions are refused before operational activation."""

import pytest

from ai_trading_lab.runtime.models import RuntimeConfig
from tests.unit.test_runtime_source import observed_config


@pytest.mark.parametrize(
    "field,value",
    [("poll_interval_seconds", 61), ("max_gap_seconds", 0), ("max_gap_seconds", 345601)],
)
def test_observed_cadence_refuses_contradictions(field: str, value: int) -> None:
    original, _ = observed_config()
    raw = original.model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(raw)


def test_poll_interval_cannot_exceed_gap() -> None:
    original, _ = observed_config()
    raw = original.model_dump(mode="json")
    raw.update(poll_interval_seconds=2, max_gap_seconds=1)
    with pytest.raises(ValueError, match="cadence"):
        RuntimeConfig.model_validate(raw)
