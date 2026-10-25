"""Synthetic persistent reducer acceptance and fail-closed regression cases."""

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest

from ai_trading_lab.market_data.models import DataMode
from ai_trading_lab.paper_execution.replay import ReplayEntry
from ai_trading_lab.runtime.engine import reducer as runtime_reducer
from ai_trading_lab.runtime.evidence import observed_quote
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.strategies.models import StrategyFrame
from tests.backtest_helpers import research
from tests.experiment_helpers import profile
from tests.unit.test_experiment_runner import tape_with_exit
from tests.unit.test_paper_replay import entry_data


def reducer(config: dict[str, Any], events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return cast(dict[str, Any], runtime_reducer(config, events))


def config() -> dict[str, Any]:
    spec = research()
    return dict(
        session_id=str(uuid4()),
        cash_unit=spec.cash_unit,
        cash_step=str(spec.cash_step),
        initial_cash=str(spec.initial_cash),
        simulation=spec.simulation.model_dump(mode="json"),
        profile=profile().model_dump(mode="json"),
        strategy=spec.strategy.model_dump(mode="json"),
        order_notional=str(spec.order_notional),
        order_ttl_seconds=spec.order_ttl_seconds,
        data_mode="FIXTURE",
        started_at_utc=spec.start_at_utc.isoformat(),
        deadline_at_utc=(spec.start_at_utc + timedelta(days=2)).isoformat(),
        poll_interval_seconds=1,
        max_gap_seconds=90000,
        max_cycles=100,
        max_journal_bytes=64_000_000,
    )


def tick(frame: StrategyFrame, index: int, source: ReplayEntry | None = None) -> dict[str, Any]:
    return dict(
        key=f"tick:{index}",
        at=frame.at_utc.isoformat(),
        payload={
            "action": "tick",
            "frame": frame.model_dump(mode="json"),
            "source_entry": source.model_dump(mode="json") if source else None,
        },
    )


def control(action: str, seconds: int, key: str = "control") -> dict[str, Any]:
    return dict(
        key=key,
        at=(research().start_at_utc + timedelta(seconds=seconds)).isoformat(),
        payload={"action": action},
    )


def test_portfolio_fees_positions_and_native_dedup_persist() -> None:
    cfg = config()
    cfg["simulation"]["fee_bps"] = "10"
    first, second, _ = tape_with_exit()
    one = reducer(cfg, (tick(first, 1),))
    assert one["portfolio"]["positions"]
    assert Decimal(one["portfolio"]["fees"]) > 0
    two = reducer(cfg, (tick(first, 1), tick(second, 2)))
    assert not two["portfolio"]["positions"]
    assert Decimal(two["portfolio"]["fees"]) > Decimal(one["portfolio"]["fees"])
    duplicate = first.model_copy(
        update={"quote": first.quote.model_copy(update={"snapshot_id": uuid4()})}
    )
    same = reducer(cfg, (tick(first, 1), tick(duplicate, 2)))
    assert same["portfolio"] == one["portfolio"]
    assert same["last_decision"]["decision"]["reason"] == "duplicate_native_event"
    assert reducer(cfg, (tick(first, 1),)) == one


def test_pause_blocks_entries_but_qualified_exit_continues() -> None:
    first, second, _ = tape_with_exit()
    cfg = config()
    state = reducer(cfg, (control("pause", 0), tick(first, 1)))
    assert state["status"] == "PAUSED" and not state["portfolio"]["positions"]
    state = reducer(cfg, (tick(first, 1), control("pause", 0), tick(second, 2)))
    assert state["status"] == "PAUSED" and not state["portfolio"]["positions"]
    with pytest.raises(ValueError, match="stopped"):
        reducer(cfg, (control("stop", 0), tick(first, 1)))


def test_loss_latches_survive_resume_and_day_open_is_persistent() -> None:
    cfg = config()
    cfg["profile"]["drawdown_fraction"] = "0.001"
    first, second, third = tape_with_exit()
    state = reducer(cfg, (tick(first, 1), tick(second, 2), control("resume", 1), tick(third, 3)))
    assert state["risk_latch"] == "drawdown_limit"
    assert not state["portfolio"]["positions"]
    assert state["last_decision"]["decision"]["reason"] == "drawdown_limit"
    later = control("resume", 86400)
    day = reducer(cfg, (tick(first, 1), tick(second, 2), later))
    assert Decimal(day["day_open_realized_net"]) == (
        Decimal(day["portfolio"]["realized_gross"]) - Decimal(day["portfolio"]["fees"])
    )
    assert day["risk_latch"] == "drawdown_limit"


def test_poll_budget_restart_and_failure_gap_require_resume() -> None:
    cfg = config()
    cfg["max_cycles"] = 2
    state = reducer(cfg, (control("poll", 0),))
    assert state["poll_attempts"] == 1 and state["poll_pending"] is True
    with pytest.raises(ValueError, match="poll_admission"):
        reducer(cfg, (control("poll", 0), control("poll", 1, "another")))
    tape = (control("poll", 0), control("failure", 1, "fail"))
    assert reducer(cfg, tape)["status"] == "DEGRADED"
    state = reducer(cfg, (*tape, control("poll", 2, "poll2"), control("failure", 3, "fail2")))
    assert state["status"] == "STOPPED" and state["poll_attempts"] == 2
    cfg["max_gap_seconds"] = 1
    assert (
        reducer(cfg, (control("pause", 2), control("resume", 2, "resume")))["status"] == "RUNNING"
    )
    assert reducer(cfg, (control("failure", 2),))["status"] == "DEGRADED"


def test_deadline_cycles_clock_corruption_and_missing_config_fail_closed() -> None:
    cfg = config()
    first = research().frames[0]
    cfg["max_cycles"] = 1
    assert reducer(cfg, (tick(first, 1),))["status"] == "STOPPED"
    cfg["deadline_at_utc"] = (first.at_utc + timedelta(seconds=1)).isoformat()
    assert reducer(cfg, (control("poll", 1),))["status"] == "STOPPED"
    with pytest.raises(ValueError):
        reducer(cfg, (control("pause", 1), control("resume", 0, "earlier")))
    del cfg["simulation"]
    with pytest.raises(ValueError):
        reducer(cfg, ())
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate({**config(), "initial_cash": 100.0})


@pytest.mark.parametrize("change", ["missing_forecast", "stale", "currency"])
def test_unqualified_inputs_abstain(change: str) -> None:
    cfg = config()
    frame = research().frames[0]
    if change == "missing_forecast":
        frame = frame.model_copy(update={"forecast": None})
    elif change == "stale":
        frame = frame.model_copy(update={"at_utc": frame.at_utc + timedelta(seconds=100)})
    else:
        cfg["cash_unit"] = "OTHER"
    result = reducer(cfg, (tick(frame, 1),))
    assert not result["portfolio"]["positions"]
    assert result["last_decision"]["decision"]["kind"] == "abstain"


def test_stale_mark_control_clock_cannot_reuse_entry_nav() -> None:
    cfg = config()
    first = research().frames[0]
    state = reducer(cfg, (tick(first, 1), control("pause", 100)))
    assert state["nav"] is None
    assert state["entry_suspended"] is True


def test_observed_raw_binding_and_native_identity_ignore_capture_uuid() -> None:
    entry = ReplayEntry.model_validate(entry_data())
    quote = observed_quote(entry)
    other = entry.model_copy(
        update={"book_capture": entry.book_capture.model_copy(update={"capture_id": uuid4()})}
    )
    assert observed_quote(other).snapshot_id == quote.snapshot_id
    cfg = config()
    cfg.update(
        data_mode="OBSERVED",
        cash_unit=quote.instrument.cash_unit,
        instrument=quote.instrument.model_dump(mode="json"),
    )
    original = research().frames[0]
    frame = original.model_copy(
        update={
            "quote": quote,
            "at_utc": quote.received_at_utc,
            "forecast": None,
            "resolution_terms_hash": entry.resolution_terms_hash,
        }
    )
    state = reducer(cfg, observed_events(frame, entry))
    assert state["last_decision"]["decision"]["reason"] == "forecast_required"
    forged = frame.model_copy(update={"quote": quote.model_copy(update={"asks": ()})})
    with pytest.raises(ValueError, match="raw_quote"):
        reducer(cfg, observed_events(forged, entry))
    forged_capture = entry.model_copy(
        update={
            "book_capture": entry.book_capture.model_copy(update={"data_mode": DataMode.FIXTURE})
        }
    )
    with pytest.raises(ValueError):
        observed_quote(forged_capture)


def test_daily_loss_equality_and_midnight_baseline() -> None:
    cfg = config()
    cfg["profile"]["daily_loss_fraction"] = "0.02"
    cfg["profile"]["drawdown_fraction"] = "0.9"
    first, second, third = tape_with_exit()
    state = reducer(cfg, (tick(first, 1), tick(second, 2), tick(third, 3)))
    assert state["risk_latch"] == "daily_loss_limit"
    assert not state["portfolio"]["positions"]
    assert state["last_decision"]["decision"]["reason"] == "daily_loss_limit"


def test_missing_and_consumed_depth_do_not_invent_nav() -> None:
    cfg = config()
    first = research().frames[0]
    at = first.at_utc + timedelta(seconds=1)
    missing = first.model_copy(
        update={
            "at_utc": at,
            "quote": first.quote.model_copy(
                update={
                    "snapshot_id": uuid4(),
                    "event_time_utc": at,
                    "received_at_utc": at,
                    "bids": (),
                }
            ),
        }
    )
    result = reducer(cfg, (tick(first, 1), tick(missing, 2)))
    assert result["nav"] is None and result["entry_suspended"] is True
    assert result["portfolio"]["positions"]


def test_observed_forecast_executes_only_with_raw_bound_history() -> None:
    entry = ReplayEntry.model_validate(entry_data())
    quote = observed_quote(entry)
    cfg = config()
    cfg.update(
        data_mode="OBSERVED",
        cash_unit=quote.instrument.cash_unit,
        instrument=quote.instrument.model_dump(mode="json"),
    )
    original = research().frames[0]
    assert original.forecast is not None
    forecast = original.forecast.model_copy(
        update={
            "instrument_key": quote.instrument.key,
            "market_id": quote.instrument.market_id,
            "outcome": quote.instrument.outcome,
            "resolution_terms_hash": entry.resolution_terms_hash,
            "data_mode": DataMode.OBSERVED,
        }
    )
    frame = original.model_copy(
        update={
            "quote": quote,
            "at_utc": quote.received_at_utc,
            "forecast": forecast,
            "resolution_terms_hash": entry.resolution_terms_hash,
        }
    )
    state = reducer(cfg, observed_events(frame, entry))
    assert state["portfolio"]["positions"]
    assert state["last_decision"]["receipt"]["quote"]["data_mode"] == "OBSERVED"
    assert state["history"][0]["payload"]["source_entry"] == entry.model_dump(mode="json")
    from ai_trading_lab.paper_execution.history import History
    from tests.unit.test_paper_replay import config as history_config

    bound = history_config()
    bound.update(runtime_session_id=cfg["session_id"], runtime_data_mode="OBSERVED")
    from uuid import UUID

    portfolio_id = UUID(state["portfolio"]["portfolio_id"])
    history = History(portfolio_id, bound)
    command = dict(state["history"][0]["payload"])
    command["quote"] = {**command["quote"], "asks": []}
    with pytest.raises(ValueError, match="raw_quote_mismatch"):
        history.apply("order", "runtime-decision:1", command, frame.at_utc)


def test_public_revalidation_and_payload_conflicts() -> None:
    cfg = config()
    event = control("pause", 0)
    event["payload"]["enable_live"] = True
    with pytest.raises(ValueError, match="unknown_payload"):
        reducer(cfg, (event,))
    first = research().frames[0]
    changed = first.model_copy(update={"quote": first.quote.model_copy(update={"bids": ()})})
    with pytest.raises(ValueError, match="native_event_conflict"):
        reducer(cfg, (tick(first, 1), tick(changed, 2)))


def observed_events(frame: StrategyFrame, entry: ReplayEntry) -> tuple[dict[str, Any], ...]:
    """Exercise observed protocol structurally; synthetic captures are not run evidence."""
    at = frame.at_utc.isoformat()
    return (
        {"key": "poll:1", "at": at, "payload": {"action": "poll"}},
        {**tick(frame, 1, entry), "key": "poll:1:result"},
    )
