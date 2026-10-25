"""Pure paper ledger accounting; fixture prices never imply executable liquidity."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from uuid import UUID

from pydantic import Field, model_validator

from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.paper_execution.models import (
    Amount,
    NonnegativeAmount,
    OrderIntent,
    PaperInstrument,
    PositiveAmount,
    SimulationPolicy,
    SimulationResult,
)

ZERO = Decimal(0)


def _round(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * step


class Position(FrozenModel):
    instrument: PaperInstrument
    quantity: PositiveAmount
    cost_basis: NonnegativeAmount


class PortfolioState(FrozenModel):
    portfolio_id: UUID
    cash_unit: str = Field(min_length=1, max_length=200)
    cash_step: PositiveAmount
    initial_cash: PositiveAmount
    cash: NonnegativeAmount
    reserved_cash: NonnegativeAmount = ZERO
    positions: tuple[Position, ...] = ()
    realized_gross: Amount = ZERO
    fees: NonnegativeAmount = ZERO
    paused: bool = False
    sequence: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def invariants(self) -> PortfolioState:
        with localcontext() as context:
            context.prec = 100
            if self.reserved_cash > self.cash:
                raise ValueError("reservation_exceeds_cash")
            for amount in (self.initial_cash, self.cash, self.reserved_cash, self.fees):
                if amount % self.cash_step:
                    raise ValueError("cash_step_mismatch")
            basis = sum((position.cost_basis for position in self.positions), ZERO)
            if self.cash + basis != self.initial_cash + self.realized_gross - self.fees:
                raise ValueError("portfolio_accounting_identity")
            keys = [position.instrument.key for position in self.positions]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate_position")
            for position in self.positions:
                instrument = position.instrument
                if instrument.cash_unit != self.cash_unit or instrument.cash_step != self.cash_step:
                    raise ValueError("currency_mismatch")
                if position.quantity % instrument.quantity_step:
                    raise ValueError("quantity_step_mismatch")
                if position.cost_basis % self.cash_step:
                    raise ValueError("basis_step_mismatch")
        return self


class JournalLine(FrozenModel):
    account: str = Field(min_length=1, max_length=300)
    unit: str = Field(min_length=1, max_length=200)
    amount: Amount


def _pair(account: str, contra: str, unit: str, amount: Decimal) -> tuple[JournalLine, ...]:
    return (
        JournalLine(account=account, unit=unit, amount=amount),
        JournalLine(account=contra, unit=unit, amount=-amount),
    )


def open_portfolio(
    portfolio_id: UUID, cash_unit: str, cash_step: Decimal, initial_cash: Decimal
) -> tuple[PortfolioState, tuple[JournalLine, ...]]:
    state = PortfolioState(
        portfolio_id=portfolio_id,
        cash_unit=cash_unit,
        cash_step=cash_step,
        initial_cash=initial_cash,
        cash=initial_cash,
    )
    with localcontext() as context:
        context.prec = 100
        return state, _pair("cash", "funding_contra", cash_unit, state.initial_cash)


def admit(state: PortfolioState, intent: OrderIntent, policy: SimulationPolicy) -> str | None:
    """Evaluate balances and explicitly supplied policy; SELL reduces held risk."""
    with localcontext() as context:
        context.prec = 100
        instrument = intent.instrument
        if state.portfolio_id != intent.portfolio_id:
            return "portfolio_mismatch"
        if instrument.cash_unit != state.cash_unit:
            return "currency_mismatch"
        if instrument.cash_step != state.cash_step:
            return "cash_step_mismatch"
        if intent.quantity % instrument.quantity_step or intent.limit_price % instrument.tick_size:
            return "order_step_mismatch"
        position = next((p for p in state.positions if p.instrument == instrument), None)
        # Equal asset units do not establish equal venue/instrument/outcome identity.
        if any(
            p.instrument.base_unit == instrument.base_unit and p.instrument != instrument
            for p in state.positions
        ):
            return "instrument_identity_mismatch"
        notional = _round(intent.quantity * intent.limit_price, state.cash_step, up=True)
        if intent.side == "sell":
            return (
                None
                if position and position.quantity >= intent.quantity
                else "insufficient_quantity"
            )
        # Reducing an existing holding is not admission of new exposure.
        if notional > policy.max_order_notional:
            return "max_order_notional"
        if state.paused:
            return "portfolio_paused"
        fee = _round(notional * policy.fee_bps / Decimal(10000), state.cash_step, up=True)
        if fee + notional > state.cash - state.reserved_cash:
            return "insufficient_cash"
        basis = position.cost_basis if position else ZERO
        if basis + notional > policy.max_position_notional:
            return "max_position_notional"
        exposure = sum((p.cost_basis for p in state.positions), ZERO)
        if exposure + notional > policy.max_total_exposure:
            return "max_total_exposure"
        if policy.fee_bps > policy.max_cost_bps:
            return "max_cost_bps"
        return None


def _project(
    state: PortfolioState,
    *,
    cash: Decimal,
    positions: list[Position],
    realized: Decimal,
    fees: Decimal,
) -> PortfolioState:
    data = state.model_dump()
    data.update(
        cash=cash,
        positions=tuple(positions),
        realized_gross=realized,
        fees=fees,
        sequence=state.sequence + 1,
    )
    return PortfolioState.model_validate(data)


def _trade_lines(
    instrument: PaperInstrument,
    quantity: Decimal,
    cash: Decimal,
    fee: Decimal,
    basis: Decimal,
    gross: Decimal,
) -> tuple[JournalLine, ...]:
    return (
        *_pair("cash", "counterparty_cash", instrument.cash_unit, cash),
        *_pair("fees", "counterparty_cash", instrument.cash_unit, fee),
        *_pair(f"asset:{instrument.key}", "counterparty_asset", instrument.base_unit, quantity),
        *_pair(f"costbasis:{instrument.key}", "basis_contra", instrument.cash_unit, basis),
        *_pair("realized_gross", "pnl_contra", instrument.cash_unit, gross),
    )


def apply_fill(
    state: PortfolioState, intent: OrderIntent, result: SimulationResult
) -> tuple[PortfolioState, tuple[JournalLine, ...]]:
    """Apply one validated IOC result. Persistence owns reservation and idempotence."""
    with localcontext() as context:
        context.prec = 100
        instrument = intent.instrument
        if (
            intent.portfolio_id != state.portfolio_id
            or instrument.cash_unit != state.cash_unit
            or instrument.cash_step != state.cash_step
        ):
            raise ValueError("portfolio_or_currency_mismatch")
        quantity, notional, fee = result.quantity, result.notional, result.fee
        if (
            quantity <= 0
            or quantity > intent.quantity
            or quantity % instrument.quantity_step
            or notional <= 0
            or notional % state.cash_step
            or fee % state.cash_step
            or result.status not in ("FILLED", "PARTIALLY_FILLED")
        ):
            raise ValueError("invalid_fill")
        if (result.status == "FILLED") != (quantity == intent.quantity):
            raise ValueError("fill_status_mismatch")
        limit_notional = quantity * intent.limit_price
        if (intent.side == "buy" and notional > limit_notional) or (
            intent.side == "sell" and notional < limit_notional
        ):
            raise ValueError("fill_limit_violation")
        position = next((p for p in state.positions if p.instrument == instrument), None)
        if any(
            p.instrument.base_unit == instrument.base_unit and p.instrument != instrument
            for p in state.positions
        ):
            raise ValueError("instrument_identity_mismatch")
        positions = [p for p in state.positions if p.instrument != instrument]
        if intent.side == "buy":
            if state.paused:
                raise ValueError("portfolio_paused")
            delta_cash = -notional - fee
            if -delta_cash > state.cash - state.reserved_cash:
                raise ValueError("insufficient_cash")
            positions.append(
                Position(
                    instrument=instrument,
                    quantity=quantity + (position.quantity if position else ZERO),
                    cost_basis=notional + (position.cost_basis if position else ZERO),
                )
            )
            basis_delta, gross, asset_delta = notional, ZERO, quantity
        else:
            if position is None or position.quantity < quantity:
                raise ValueError("insufficient_quantity")
            released = (
                position.cost_basis
                if quantity == position.quantity
                else _round(
                    position.cost_basis * quantity / position.quantity, state.cash_step, up=False
                )
            )
            if quantity < position.quantity:
                positions.append(
                    Position(
                        instrument=instrument,
                        quantity=position.quantity - quantity,
                        cost_basis=position.cost_basis - released,
                    )
                )
            delta_cash = notional - fee
            basis_delta, gross, asset_delta = -released, notional - released, -quantity
        new_state = _project(
            state,
            cash=state.cash + delta_cash,
            positions=positions,
            realized=state.realized_gross + gross,
            fees=state.fees + fee,
        )
        return new_state, _trade_lines(instrument, asset_delta, delta_cash, fee, basis_delta, gross)


def settle_position(
    state: PortfolioState, instrument: PaperInstrument, payout_per_unit: Decimal, void: bool = False
) -> tuple[PortfolioState, tuple[JournalLine, ...]]:
    with localcontext() as context:
        context.prec = 100
        # Validate even void metadata, rejecting floats and nonfinite fixture values.
        payout = PortfolioPayout(value=payout_per_unit).value
        position = next((p for p in state.positions if p.instrument == instrument), None)
        if instrument.kind != "prediction" or position is None:
            raise ValueError("prediction_holding_required")
        proceeds = (
            position.cost_basis
            if void
            else _round(position.quantity * payout, state.cash_step, up=False)
        )
        gross = proceeds - position.cost_basis
        new_state = _project(
            state,
            cash=state.cash + proceeds,
            positions=[p for p in state.positions if p.instrument != instrument],
            realized=state.realized_gross + gross,
            fees=state.fees,
        )
        return new_state, _trade_lines(
            instrument, -position.quantity, proceeds, ZERO, -position.cost_basis, gross
        )


class PortfolioPayout(FrozenModel):
    value: NonnegativeAmount = Field(le=1)


def reconcile_journal(state: PortfolioState, lines: Sequence[JournalLine]) -> bool:
    """Check complete journal history against projections; never repair a mismatch."""
    try:
        state = PortfolioState.model_validate(state.model_dump())
        with localcontext() as context:
            context.prec = 100
            balances: defaultdict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
            units: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
            cash_accounts = {
                "cash",
                "funding_contra",
                "counterparty_cash",
                "fees",
                "basis_contra",
                "realized_gross",
                "pnl_contra",
            }
            for line in lines:
                line = JournalLine.model_validate(line.model_dump())
                account = line.account
                if account in cash_accounts:
                    if line.unit != state.cash_unit:
                        return False
                elif account.startswith("costbasis:"):
                    if (
                        line.unit != state.cash_unit
                        or len(account.removeprefix("costbasis:")) != 64
                    ):
                        return False
                elif account.startswith("asset:"):
                    if len(account.removeprefix("asset:")) != 64 or line.unit == state.cash_unit:
                        return False
                elif account == "counterparty_asset":
                    if line.unit == state.cash_unit:
                        return False
                else:
                    return False
                units[line.unit] += line.amount
                balances[(account, line.unit)] += line.amount
            if any(units.values()) or state.reserved_cash != 0:
                return False
            expected = {
                "cash": state.cash,
                "funding_contra": -state.initial_cash,
                "fees": state.fees,
                "realized_gross": state.realized_gross,
            }
            if any(
                balances[(name, state.cash_unit)] != amount for name, amount in expected.items()
            ):
                return False
            total_basis = sum((p.cost_basis for p in state.positions), ZERO)
            contra_expected = {
                "counterparty_cash": state.initial_cash - state.cash - state.fees,
                "basis_contra": -total_basis,
                "pnl_contra": -state.realized_gross,
            }
            if any(
                balances[(name, state.cash_unit)] != amount
                for name, amount in contra_expected.items()
            ):
                return False
            held_units: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
            for position in state.positions:
                held_units[position.instrument.base_unit] += position.quantity
            asset_units = {unit for account, unit in balances if account == "counterparty_asset"}
            if any(
                balances[("counterparty_asset", unit)] != -held_units[unit]
                for unit in asset_units | set(held_units)
            ):
                return False
            projected: dict[tuple[str, str], Decimal] = {}
            for position in state.positions:
                instrument = position.instrument
                projected[(f"asset:{instrument.key}", instrument.base_unit)] = position.quantity
                projected[(f"costbasis:{instrument.key}", state.cash_unit)] = position.cost_basis
            return all(
                amount == projected.get((account, unit), ZERO)
                for (account, unit), amount in balances.items()
                if account.startswith(("asset:", "costbasis:"))
            ) and all(balances[key] == amount for key, amount in projected.items())
    except (ValueError, ArithmeticError):
        return False
