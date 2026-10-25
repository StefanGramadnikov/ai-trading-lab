"""Clock and capacity regressions with wholly synthetic quotes."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from ai_trading_lab.strategies.models import StrategyFrame
from tests.backtest_helpers import research
from tests.unit.test_runtime_engine import config, reducer, tick


def shifted_frame(at: datetime) -> StrategyFrame:
    frame = research().frames[0]
    assert frame.forecast is not None
    return frame.model_copy(
        update={
            "at_utc": at,
            "quote": frame.quote.model_copy(update={"event_time_utc": at, "received_at_utc": at}),
            "forecast": frame.forecast.model_copy(
                update={"produced_at_utc": at, "available_at_utc": at}
            ),
        }
    )


@pytest.mark.parametrize("offset", [UTC, timezone(timedelta(hours=2))])
def test_latency_midnight_pause_cannot_rewind_daily_baseline(offset: Any) -> None:
    at = datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC).astimezone(offset)
    cfg = config()
    cfg.update(started_at_utc=at.isoformat(), deadline_at_utc=(at + timedelta(days=1)).isoformat())
    cfg["simulation"].update(latency_ms=2000, fee_bps="100")
    first = tick(shifted_frame(at), 1)
    pause = dict(
        key="pause", at=(at + timedelta(milliseconds=500)).isoformat(), payload={"action": "pause"}
    )
    resume = dict(
        key="resume", at=(at + timedelta(seconds=2)).isoformat(), payload={"action": "resume"}
    )
    initial = reducer(cfg, (first,))
    assert Decimal(initial["portfolio"]["fees"]) > 0
    assert initial["day"] == "2026-01-02"
    paused = reducer(cfg, (first, pause))
    assert paused["day"] == initial["day"]
    assert paused["day_open_realized_net"] == initial["day_open_realized_net"] == "0"
    resumed = reducer(cfg, (first, pause, resume))
    assert resumed["day_open_realized_net"] == "0"
    assert resumed["portfolio"] == initial["portfolio"]


@pytest.mark.parametrize("seconds", [1, 2])
def test_capacity_reason_survives_deadline_and_gap(seconds: int) -> None:
    cfg = config()
    at = datetime.fromisoformat(cfg["started_at_utc"])
    cfg.update(deadline_at_utc=(at + timedelta(seconds=1)).isoformat(), max_gap_seconds=1)
    event = dict(
        key="capacity",
        at=(at + timedelta(seconds=seconds)).isoformat(),
        payload={"action": "stop", "reason": "capacity"},
    )
    state = reducer(cfg, (event,))
    assert state["status"] == "STOPPED"
    assert state["suspension_reason"] == "capacity"


def test_tick_after_clock_gap_blocks_new_entry() -> None:
    cfg = config()
    cfg["max_gap_seconds"] = 1
    at = datetime.fromisoformat(cfg["started_at_utc"]) + timedelta(seconds=2)
    state = reducer(cfg, (tick(shifted_frame(at), 1),))
    assert state["last_decision"]["decision"]["reason"] == "clock_gap"
    assert not state["portfolio"]["positions"]
