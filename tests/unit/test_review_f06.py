"""Command chronology stays separate from simulated execution chronology."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from ai_trading_lab.paper_execution.history import History
from tests.backtest_helpers import research
from tests.integration.test_paper_store import NOW, asset, intent, policy, quote
from tests.unit.test_runtime_engine import config, control, reducer, tick


def test_f06_pause_during_simulated_latency_is_accepted() -> None:
    pid = uuid4()
    history = History(
        pid,
        {
            "cash_unit": "USD",
            "cash_step": "0.01",
            "initial_cash": "100",
            "policy": policy().model_copy(update={"latency_ms": 2000}).model_dump(mode="json"),
        },
    )
    instrument = asset(True)
    order = intent(pid, quantity="10", price="0.50", instrument=instrument)
    effect = history.apply(
        "order",
        "buy",
        {
            "intent": order.model_dump(mode="json"),
            "quote": quote(instrument, "0.50", "100").model_dump(mode="json"),
        },
        NOW,
    )
    assert effect.receipt is not None
    assert effect.receipt.result.quantity == Decimal("10")
    assert effect.at == NOW + timedelta(seconds=2)
    history.apply("pause", "pause-during-latency", {"paused": True}, NOW + timedelta(seconds=1))
    assert history.state.paused
    assert history.last_at == NOW + timedelta(seconds=1)


def test_f06_runtime_poll_and_pause_do_not_inherit_execution_clock() -> None:
    cfg = config()
    cfg["simulation"]["latency_ms"] = 2000
    frame = research().frames[0]
    state = reducer(cfg, (tick(frame, 1), control("poll", 1, "poll"), control("pause", 1, "pause")))
    assert state["status"] == "PAUSED"
    assert state["poll_pending"] is True
    assert state["portfolio"]["positions"]
