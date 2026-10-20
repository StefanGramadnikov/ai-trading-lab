from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from ai_trading_lab.reporting.cli import prepare


def test_operational_templates_deduplicate_and_preserve_unknown() -> None:
    report: dict[str, Any] = {
        "experiment_id": str(uuid4()),
        "lifecycle": {
            "last_sequence": 2,
            "as_of_utc": "2026-10-20T00:00:00+00:00",
            "state": "PAUSED",
            "completed_cells": 1,
            "total_cells": 2,
        },
        "consolidated_economic_net_pnl": None,
        "reporting_currency": "USD",
        "cells": [
            {
                "cell_id": "rules",
                "entry_suspended": True,
                "ai_metrics": {"failed_results": 1},
                "inference_budget": {
                    "spent_estimated": "0.8",
                    "reserved": "0.1",
                    "total_limit": "1",
                    "unresolved": 1,
                    "currency": "USD",
                },
            }
        ],
    }
    at = datetime(2026, 10, 20, tzinfo=UTC)
    result = prepare(report, at, Decimal("0.9"))
    assert [n.kind for n in result] == ["daily_summary", "safety_halt", "failure", "budget_warning"]
    assert "unavailable" in result[0].text
    assert result == prepare(report, at, Decimal("0.9"))
    assert len({n.event_key for n in result}) == 4
    for bad in ("0", "1.1", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            prepare(report, at, Decimal(bad))
    with pytest.raises(ValueError):
        prepare(report, datetime(2026, 10, 20), Decimal("0.9"))


def test_same_checkpoint_later_scan_reuses_every_event() -> None:
    from datetime import timedelta

    report = {
        "experiment_id": str(uuid4()),
        "lifecycle": {
            "last_sequence": 3,
            "as_of_utc": "2026-10-20T08:00:00+00:00",
            "state": "PAUSED",
            "completed_cells": 1,
            "total_cells": 2,
        },
        "consolidated_economic_net_pnl": None,
        "reporting_currency": "USD",
        "cells": [
            {
                "cell_id": "hybrid",
                "entry_suspended": True,
                "ai_metrics": {"failed_results": 1},
                "inference_budget": {
                    "spent_estimated": "0.9",
                    "reserved": "0",
                    "total_limit": "1",
                    "unresolved": 0,
                    "currency": "USD",
                },
            }
        ],
    }
    at = datetime(2026, 10, 20, 9, tzinfo=UTC)
    first = prepare(report, at, Decimal("0.8"))
    assert first == prepare(report, at + timedelta(hours=1), Decimal("0.8"))
    tomorrow = prepare(report, at + timedelta(days=1), Decimal("0.8"))
    assert first[0].event_key != tomorrow[0].event_key
    assert first[1:] == tomorrow[1:]
    with pytest.raises(ValueError, match="predates"):
        prepare(report, at - timedelta(hours=2), Decimal("0.8"))
