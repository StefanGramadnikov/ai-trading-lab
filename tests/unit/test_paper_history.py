"""Offline command replay validates economic and control projections."""

from datetime import timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.store import events, journal, liquidity, orders, projections
from tests.integration.test_paper_store import NOW, asset, intent, policy, quote


def history() -> History:
    return History(
        uuid4(),
        dict(
            cash_unit="USD",
            cash_step="0.01",
            initial_cash="100",
            policy=policy().model_dump(mode="json"),
        ),
    )


def test_command_replay_recovers_cash_controls_receipts_and_shared_capacity() -> None:
    ledger = history()
    pid = ledger.state.portfolio_id
    snapshot = quote(capacity="2")
    command: dict[str, object] = dict(
        intent=intent(pid).model_dump(mode="json"), quote=snapshot.model_dump(mode="json")
    )
    first = ledger.apply("order", "buy", command, NOW)
    assert first.receipt is not None and first.receipt.result.quantity == 1
    ledger.apply("pause", "stop", {"paused": True}, NOW)
    exit_order = intent(pid, "exit", side="sell")
    ledger.apply(
        "order",
        "exit",
        dict(intent=exit_order.model_dump(mode="json"), quote=snapshot.model_dump(mode="json")),
        NOW,
    )
    assert ledger.state.cash == 100 and not ledger.state.positions and ledger.state.paused
    rebuilt = History(
        pid,
        dict(
            cash_unit="USD",
            cash_step="0.01",
            initial_cash="100",
            policy=policy().model_dump(mode="json"),
        ),
    )
    for effect in ledger.effects.values():
        rebuilt.apply(effect.kind, effect.key, effect.payload, effect.submitted_at)
    assert projections(rebuilt) == projections(ledger)
    projected = projections(rebuilt)
    assert len(projected[orders.name]) == 2
    assert len(projected[events.name]) == 9
    assert projected[liquidity.name][0]["buy"] == ["2"]
    assert projected[liquidity.name][0]["sell"] == ["2"]
    assert (
        sum(cast(Decimal, row["amount"]) for row in projected[journal.name] if row["unit"] == "USD")
        == 0
    )


def test_rejected_and_partial_commands_replay_with_original_provenance() -> None:
    ledger = history()
    pid = ledger.state.portfolio_id
    snapshot = quote(capacity="0.5")
    request = intent(pid)
    effect = ledger.apply(
        "order",
        "buy",
        dict(intent=request.model_dump(mode="json"), quote=snapshot.model_dump(mode="json")),
        NOW,
    )
    assert effect.receipt is not None
    assert effect.receipt.quote == snapshot
    assert effect.receipt.submitted_at_utc == NOW
    assert effect.receipt.result.status == "PARTIALLY_FILLED"
    again = intent(pid, "again")
    effect = ledger.apply(
        "order",
        "again",
        dict(intent=again.model_dump(mode="json"), quote=snapshot.model_dump(mode="json")),
        NOW,
    )
    assert effect.receipt is not None and effect.receipt.result.quantity == 0
    assert ledger.state.cash == 95
    with pytest.raises(ValueError, match="Duplicate"):
        ledger.apply(
            "order",
            "again",
            dict(intent=again.model_dump(mode="json"), quote=snapshot.model_dump(mode="json")),
            NOW,
        )


def test_settlement_control_and_native_definition_recovery() -> None:
    ledger = history()
    instrument = asset(True)
    request = intent(ledger.state.portfolio_id, instrument=instrument, price="0.50")
    ledger.apply(
        "order",
        "buy",
        dict(
            intent=request.model_dump(mode="json"),
            quote=quote(instrument, "0.50").model_dump(mode="json"),
        ),
        NOW,
    )
    result = ledger.apply(
        "settle",
        "resolve",
        dict(instrument=instrument.model_dump(mode="json"), payout_per_unit="1", void=False),
        NOW,
    )
    assert result.state.cash == Decimal("100.50")
    assert not result.state.positions
    with pytest.raises(ValueError, match="already settled"):
        ledger.apply(
            "settle",
            "double",
            dict(instrument=instrument.model_dump(mode="json"), payout_per_unit="1", void=False),
            NOW,
        )
    assert all(settled for _, settled in ledger.definitions.values())
    with pytest.raises(ValueError, match="backwards"):
        ledger.apply("pause", "rewind", {"paused": True}, NOW - timedelta(seconds=1))
