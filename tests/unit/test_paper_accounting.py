"""Synthetic paper fixtures prove accounting invariants, not trading performance."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_trading_lab.paper_execution.accounting import (
    JournalLine,
    PortfolioState,
    Position,
    admit,
    apply_fill,
    open_portfolio,
    reconcile_journal,
    settle_position,
)
from ai_trading_lab.paper_execution.models import (
    OrderIntent,
    PaperInstrument,
    SimulationPolicy,
    SimulationResult,
)

D = Decimal
PID = UUID(int=71)
NOW = datetime(2026, 10, 18, tzinfo=UTC)


def instrument(**updates: object) -> PaperInstrument:
    data: dict[str, object] = dict(
        source="kalshi",
        venue="fixture",
        native_id="yes-71",
        kind="prediction",
        base_unit="contract-71",
        cash_unit="USD",
        market_id="event-71",
        outcome="yes",
        tick_size="0.01",
        quantity_step="1",
        cash_step="0.01",
    )
    data.update(updates)
    return PaperInstrument.model_validate(data)


def intent(
    side: str = "buy",
    quantity: str = "3",
    price: str = "0.33",
    asset: PaperInstrument | None = None,
) -> OrderIntent:
    return OrderIntent(
        portfolio_id=PID,
        idempotency_key="fixture",
        instrument=asset or instrument(),
        side=side,
        quantity=D(quantity),
        limit_price=D(price),
        created_at_utc=NOW,
        expires_at_utc=NOW + timedelta(minutes=1),
    )


def result(
    quantity: str = "3", notional: str = "0.99", fee: str = "0.01", status: str = "FILLED"
) -> SimulationResult:
    return SimulationResult(
        status=status,
        reason="synthetic_fixture",
        quantity=D(quantity),
        notional=D(notional),
        fee=D(fee),
        consumed=(),
        executed_at_utc=NOW,
    )


def policy(**updates: object) -> SimulationPolicy:
    data: dict[str, object] = dict(
        version="synthetic-fixture-v1",
        fee_bps="100",
        latency_ms=0,
        max_age_seconds=10,
        future_skew_seconds=0,
        max_order_notional="100",
        max_position_notional="100",
        max_total_exposure="100",
        max_cost_bps="100",
    )
    data.update(updates)
    return SimulationPolicy.model_validate(data)


def opening(cash: str = "10") -> tuple[PortfolioState, tuple[JournalLine, ...]]:
    return open_portfolio(PID, "USD", D("0.01"), D(cash))


def test_opening_is_balanced_and_exact() -> None:
    state, lines = opening()
    assert reconcile_journal(state, lines)
    assert [(line.account, line.amount) for line in lines] == [
        ("cash", D(10)),
        ("funding_contra", D(-10)),
    ]
    with pytest.raises(ValidationError):
        opening("1.001")
    with pytest.raises(ValidationError):
        open_portfolio(PID, "USD", D("0.01"), D("NaN"))


def test_partial_then_full_sales_release_all_basis_and_keep_fees_separate() -> None:
    state, history = opening()
    state, lines = apply_fill(state, intent(), result())
    history += lines
    assert state.cash == D("9")
    assert state.positions[0].cost_basis == D("0.99")
    state, lines = apply_fill(state, intent("sell", "1", "0.50"), result("1", "0.50", "0.01"))
    history += lines
    assert state.positions[0].cost_basis == D("0.66")
    assert state.realized_gross == D("0.17")
    state, lines = apply_fill(state, intent("sell", "2", "0.50"), result("2", "1.00", "0.01"))
    history += lines
    assert not state.positions
    assert state.realized_gross == D("0.51")
    assert state.fees == D("0.03")
    assert state.cash == state.initial_cash + state.realized_gross - state.fees
    assert reconcile_journal(state, history)


def test_weighted_average_fractional_release_rounds_down_and_final_sale_removes_residue() -> None:
    state, history = opening()
    state, lines = apply_fill(state, intent(price="0.34"), result(notional="1.00"))
    history += lines
    state, lines = apply_fill(state, intent("sell", "1", "0.50"), result("1", "0.50", "0"))
    history += lines
    assert state.positions[0].cost_basis == D("0.67")
    state, lines = apply_fill(state, intent("sell", "2", "0.50"), result("2", "1.00", "0"))
    assert state.realized_gross == D("0.50")
    assert reconcile_journal(state, history + lines)


def test_multiple_buys_accumulate_weighted_basis() -> None:
    state, history = opening()
    for price, notional in [("0.33", "0.99"), ("0.50", "1.50")]:
        state, lines = apply_fill(state, intent(price=price), result(notional=notional))
        history += lines
    assert state.positions[0].quantity == 6
    assert state.positions[0].cost_basis == D("2.49")
    assert state.fees == D("0.02")
    assert reconcile_journal(state, history)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"max_order_notional": "0.98"}, "max_order_notional"),
        ({"max_position_notional": "0.98"}, "max_position_notional"),
        ({"max_total_exposure": "0.98"}, "max_total_exposure"),
        ({"max_cost_bps": "99"}, "max_cost_bps"),
    ],
)
def test_explicit_risk_limits(updates: dict[str, object], reason: str) -> None:
    state, _ = opening()
    assert admit(state, intent(), policy(**updates)) == reason


def test_worst_case_fee_reservation_and_cash_budget() -> None:
    state, _ = opening("1.00")
    assert admit(state, intent(), policy()) is None
    reserved = PortfolioState.model_validate({**state.model_dump(), "reserved_cash": D("0.01")})
    assert admit(reserved, intent(), policy()) == "insufficient_cash"
    with pytest.raises(ValueError, match="insufficient_cash"):
        apply_fill(reserved, intent(), result())


def test_pause_blocks_entries_but_allows_reducing_exit_despite_exposure_limits() -> None:
    state, _ = opening()
    state, _ = apply_fill(state, intent(), result())
    state = PortfolioState.model_validate({**state.model_dump(), "paused": True})
    assert admit(state, intent(), policy()) == "portfolio_paused"
    tight = policy(max_position_notional="0.01", max_total_exposure="0.01")
    assert admit(state, intent("sell", "3", "0.33"), tight) is None
    state, _ = apply_fill(state, intent("sell"), result())
    assert not state.positions


def test_currency_steps_exact_identity_and_available_quantity() -> None:
    state, _ = opening()
    assert admit(state, intent(asset=instrument(cash_unit="EUR")), policy()) == "currency_mismatch"
    assert admit(state, intent(asset=instrument(cash_step="0.1")), policy()) == "cash_step_mismatch"
    assert admit(state, intent(quantity="1.5"), policy()) == "order_step_mismatch"
    assert admit(state, intent("sell"), policy()) == "insufficient_quantity"
    state, _ = apply_fill(state, intent(), result())
    other = instrument(native_id="other-native")
    assert admit(state, intent("sell", asset=other), policy()) == "instrument_identity_mismatch"
    with pytest.raises(ValueError, match="identity"):
        apply_fill(state, intent("sell", asset=other), result())


@pytest.mark.parametrize(
    ("quantity", "notional", "fee", "status"),
    [
        ("4", "0.99", "0", "FILLED"),
        ("0", "0", "0", "CANCELLED"),
        ("3", "1.00", "0", "FILLED"),
        ("3", "0.99", "0.001", "FILLED"),
        ("2", "0.66", "0", "FILLED"),
        ("3", "0.99", "0", "REJECTED"),
    ],
)
def test_tampered_fills_rejected(quantity: str, notional: str, fee: str, status: str) -> None:
    state, _ = opening()
    with pytest.raises(ValueError):
        apply_fill(state, intent(), result(quantity, notional, fee, status))


@pytest.mark.parametrize(
    ("payout", "void", "cash", "gross"),
    [
        ("1", False, "12", "2.01"),
        ("0", False, "9", "-0.99"),
        ("1", True, "9.99", "0"),
    ],
)
def test_fixture_settlement_win_loss_void(payout: str, void: bool, cash: str, gross: str) -> None:
    state, history = opening()
    state, lines = apply_fill(state, intent(), result())
    history += lines
    state, lines = settle_position(state, instrument(), D(payout), void)
    assert state.cash == D(cash)
    assert state.realized_gross == D(gross)
    assert state.fees == D("0.01")
    assert not state.positions
    assert reconcile_journal(state, history + lines)
    with pytest.raises(ValueError, match="holding"):
        settle_position(state, instrument(), D(payout))


def test_settlement_validation_and_unresolved_positions() -> None:
    state, _ = opening()
    state, _ = apply_fill(state, intent(), result())
    for bad in ("NaN", "-1", "1.01"):
        with pytest.raises(ValueError):
            settle_position(state, instrument(), D(bad))
    assert state.positions[0].quantity == 3
    spot = instrument(kind="spot")
    spot_state = PortfolioState.model_validate(
        {
            **state.model_dump(),
            "positions": (Position(instrument=spot, quantity=D(3), cost_basis=D("0.99")),),
        }
    )
    with pytest.raises(ValueError, match="prediction"):
        settle_position(spot_state, spot, D(1))


def test_journal_rejects_missing_unbalanced_foreign_unknown_and_projection_changes() -> None:
    state, history = opening()
    assert not reconcile_journal(state, history[:-1])
    assert not reconcile_journal(
        state, history + (JournalLine(account="cash", unit="EUR", amount=0),)
    )
    assert not reconcile_journal(
        state, history + (JournalLine(account="unknown", unit="USD", amount=0),)
    )
    corrupt = state.model_copy(update={"cash": D("9")})
    assert not reconcile_journal(corrupt, history)
    state, lines = apply_fill(state, intent(), result())
    assert not reconcile_journal(state, history)
    assert reconcile_journal(state, history + lines)
    corrupt = state.model_copy(update={"fees": D("0.02")})
    assert not reconcile_journal(corrupt, history + lines)


def test_arbitrary_ambient_precision_does_not_change_accounting_or_identity() -> None:
    with localcontext() as context:
        context.prec = 3
        state, history = opening("123456789.12")
        state, lines = apply_fill(state, intent(), result())
        history += lines
        assert state.cash == D("123456788.12")
        state, lines = settle_position(state, instrument(), D(1))
        assert state.cash == D("123456791.12")
        assert reconcile_journal(state, history + lines)


def test_journal_rejects_balanced_contra_account_tampering() -> None:
    state, history = opening()
    tampered = history + (
        JournalLine(account="basis_contra", unit="USD", amount=D(1)),
        JournalLine(account="pnl_contra", unit="USD", amount=D(-1)),
    )
    assert not reconcile_journal(state, tampered)


def test_partial_fill_and_insufficient_sell_cash_cannot_create_negative_balance() -> None:
    state, history = opening()
    state, lines = apply_fill(state, intent(), result("2", "0.66", "0.01", "PARTIALLY_FILLED"))
    assert state.positions[0].quantity == 2
    assert reconcile_journal(state, history + lines)
    with pytest.raises(ValueError, match="insufficient_quantity"):
        apply_fill(state, intent("sell"), result())
    with pytest.raises(ValueError):
        apply_fill(state, intent("sell", "2"), result("2", "0.66", "11"))


def test_aggregate_existing_basis_limits_and_mismatched_portfolio() -> None:
    state, _ = opening()
    state, _ = apply_fill(state, intent(), result())
    assert admit(state, intent(), policy(max_position_notional="1.50")) == "max_position_notional"
    assert admit(state, intent(), policy(max_total_exposure="1.50")) == "max_total_exposure"
    wrong = intent().model_copy(update={"portfolio_id": UUID(int=999)})
    assert admit(state, wrong, policy()) == "portfolio_mismatch"
    with pytest.raises(ValueError, match="mismatch"):
        apply_fill(state, wrong, result())


def test_unrounded_limit_boundary_rejects_cash_step_price_violation() -> None:
    state, _ = opening()
    asset = instrument(quantity_step="0.1")
    with pytest.raises(ValueError, match="limit"):
        apply_fill(
            state, intent(quantity="0.1", price="0.33", asset=asset), result("0.1", "0.04", "0")
        )
    with pytest.raises(ValueError, match="reservation"):
        PortfolioState.model_validate({**state.model_dump(), "reserved_cash": D(11)})
