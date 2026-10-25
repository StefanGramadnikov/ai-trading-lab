"""Pure per-cycle decisions for the rules strategy and the passive benchmark.

Inputs are already-validated evidence, ledger state and risk state; outputs are decisions with an
optional order plan. Nothing here performs I/O, and no AI output reaches sizing or risk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from ai_trading_lab.live.config import LiveConfig, RiskConfig
from ai_trading_lab.live.risk import (
    entry_gate,
    exit_reason,
    marketable_limit,
    size_entry,
)
from ai_trading_lab.live.signals import breakout_signal
from ai_trading_lab.live.store import LoopState
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import DataQuality, FrozenModel
from ai_trading_lab.paper_execution.accounting import PortfolioState, Position
from ai_trading_lab.paper_execution.history import identity
from ai_trading_lab.paper_execution.kraken_evidence import KrakenEntry
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    PaperInstrument,
    PositiveAmount,
    SimulationPolicy,
)
from ai_trading_lab.paper_execution.simulation import simulate


@dataclass(frozen=True)
class Market:
    """One pair's validated evidence for this cycle."""

    pair: str
    entry: KrakenEntry
    quote: ExecutionQuote
    rules: Mapping[str, JsonValue]


@dataclass(frozen=True)
class Mark:
    """What a whole position would fetch now: its average bid price and the net proceeds."""

    price: Decimal
    net: Decimal


class OrderPlan(FrozenModel):
    side: Literal["buy", "sell"]
    quantity: PositiveAmount
    limit_price: PositiveAmount


class Decision(FrozenModel):
    pair: str
    action: Literal["buy", "sell", "hold"]
    reason: str
    order: OrderPlan | None = None
    # Why, beyond the short reason: the AI call behind it, its answer and what it cost.
    detail: dict[str, JsonValue] | None = None


class Proposal(FrozenModel):
    """What the AI proposed for one pair. It carries no size, price or limit: only a direction."""

    action: Literal["buy", "sell", "hold"]
    reason: str
    detail: dict[str, JsonValue] | None = None
    # False when the question was never put (no data, a local refusal): it is asked again soon.
    reviewed: bool = True


def liquidation(
    position: Position, quote: ExecutionQuote, policy: SimulationPolicy, now: datetime
) -> Mark | None:
    """Value a position by simulating its full sale on the current book; None if it cannot be."""
    instrument = position.instrument
    intent = OrderIntent(
        portfolio_id=UUID(int=0),
        idempotency_key="mark",
        instrument=instrument,
        side="sell",
        quantity=position.quantity,
        limit_price=instrument.tick_size,
        created_at_utc=now,
        expires_at_utc=now + timedelta(seconds=60),
    )
    result = simulate(intent, quote, policy.model_copy(update={"latency_ms": 0}), now, ())
    if result.status != "FILLED" or result.quantity != position.quantity:
        return None
    return Mark(price=result.notional / result.quantity, net=result.notional - result.fee)


def held_position(state: PortfolioState, instrument: PaperInstrument) -> Position | None:
    wanted = identity(instrument)
    return next((p for p in state.positions if identity(p.instrument) == wanted), None)


def _hold(pair: str, reason: str) -> Decision:
    return Decision(pair=pair, action="hold", reason=reason)


def _usable(market: Market | None, pair: str) -> Decision | None:
    if market is None:
        return _hold(pair, "no_market_data")
    if market.quote.quality != DataQuality.VALID:
        return _hold(pair, "quote_not_valid")
    if not market.quote.bids or not market.quote.asks:
        return _hold(pair, "one_sided_book")
    return None


def _entry_quantity(
    market: Market,
    *,
    equity: Decimal,
    exposure_basis: Decimal,
    cash: Decimal,
    policy_cap: Decimal,
    fee_bps: Decimal,
    risk: RiskConfig,
    limit_price: Decimal,
) -> Decimal | None:
    return size_entry(
        equity=equity,
        exposure_basis=exposure_basis,
        cash=cash,
        limit_price=limit_price,
        quantity_step=market.quote.instrument.quantity_step,
        order_min=Decimal(str(market.rules["ordermin"])),
        cost_min=Decimal(str(market.rules["costmin"])),
        policy_cap=policy_cap,
        fee_bps=fee_bps,
        risk=risk,
    )


def decide_rules(
    *,
    config: LiveConfig,
    now: datetime,
    markets: Mapping[str, Market],
    candles: Mapping[str, Sequence[Candle]],
    state: PortfolioState,
    loop: LoopState,
    marks: Mapping[str, Mark | None],
    equity: Decimal | None,
    blocked: str | None,
) -> list[Decision]:
    """Exits first (never blocked by risk state), then entries for pairs that pass every gate."""
    risk = config.risk
    exposure = sum((p.cost_basis for p in state.positions), Decimal(0))
    cash_left = state.cash - state.reserved_cash
    fee_rate = config.fee_bps / Decimal(10000)
    decisions: list[Decision] = []
    for pair in config.pairs:
        market = markets.get(pair)
        if (problem := _usable(market, pair)) is not None:
            decisions.append(problem)
            continue
        assert market is not None
        quote, tick = market.quote, market.quote.instrument.tick_size
        position = held_position(state, quote.instrument)
        history = candles.get(pair, ())
        if position is not None:
            mark = marks.get(pair)
            entry_price = position.cost_basis / position.quantity
            # Unknown entry time means unknown age: close it rather than hold it indefinitely.
            entered = loop.entered_at.get(pair, now - timedelta(hours=risk.max_hold_hours))
            # A position that cannot be fully valued (a thin or stressed book) must still be able
            # to stop out: mechanical exits fall back to the touch price. The sale then fills as
            # deep as the book allows.
            price = mark.price if mark is not None else quote.bids[0].price
            reason: str | None = exit_reason(entry_price, price, entered, now, risk)
            if reason is None:
                if mark is None:
                    decisions.append(_hold(pair, "unmarkable_position"))
                    continue
                signal = breakout_signal(history, held=True, now=now, config=config.breakout)
                reason = signal.reason if signal.action == "sell" else None
            if reason is None:
                decisions.append(_hold(pair, "holding"))
                continue
            limit = marketable_limit("sell", quote.bids[0].price, tick, risk.limit_buffer_bps)
            decisions.append(
                Decision(
                    pair=pair,
                    action="sell",
                    reason=reason,
                    order=OrderPlan(side="sell", quantity=position.quantity, limit_price=limit),
                )
            )
            continue
        if (
            why := entry_gate(
                blocked=blocked,
                held=False,
                last_entry_at=loop.last_entry_at.get(pair),
                last_stop_at=loop.last_stop_at.get(pair),
                now=now,
                risk=risk,
            )
        ) is not None:
            decisions.append(_hold(pair, why))
            continue
        signal = breakout_signal(history, held=False, now=now, config=config.breakout)
        if signal.action != "buy":
            decisions.append(_hold(pair, signal.reason))
            continue
        if equity is None:
            decisions.append(_hold(pair, "equity_unavailable"))
            continue
        limit = marketable_limit("buy", quote.asks[0].price, tick, risk.limit_buffer_bps)
        quantity = _entry_quantity(
            market,
            equity=equity,
            exposure_basis=exposure,
            cash=cash_left,
            policy_cap=config.policy().max_order_notional,
            fee_bps=config.fee_bps,
            risk=risk,
            limit_price=limit,
        )
        if quantity is None:
            decisions.append(_hold(pair, "size_below_minimum_or_no_room"))
            continue
        # Later pairs in this cycle see what the earlier ones will already use.
        exposure += quantity * limit
        cash_left -= quantity * limit * (1 + fee_rate)
        decisions.append(
            Decision(
                pair=pair,
                action="buy",
                reason=signal.reason,
                order=OrderPlan(side="buy", quantity=quantity, limit_price=limit),
            )
        )
    return decisions


def decide_benchmark(
    *,
    config: LiveConfig,
    now: datetime,
    markets: Mapping[str, Market],
    state: PortfolioState,
    loop: LoopState,
    blocked: str | None = None,
) -> list[Decision]:
    """Spend an equal share of capital on each pair once, then hold. It never sells.

    A partial entry counts as held and is not topped up; a refused or unfilled entry is retried
    in the next slot. `blocked` (for example near the ledger's capacity) stops new entries only.
    """
    share = config.initial_cash / len(config.pairs)
    whole = RiskConfig(position_fraction=Decimal(1), exposure_fraction=Decimal(1))
    decisions: list[Decision] = []
    for pair in config.pairs:
        market = markets.get(pair)
        if (problem := _usable(market, pair)) is not None:
            decisions.append(problem)
            continue
        assert market is not None
        if held_position(state, market.quote.instrument) is not None or pair in loop.entered_at:
            decisions.append(_hold(pair, "benchmark_holding"))
            continue
        if blocked is not None:
            decisions.append(_hold(pair, blocked))
            continue
        limit = marketable_limit(
            "buy", market.quote.asks[0].price, market.quote.instrument.tick_size, 10
        )
        quantity = _entry_quantity(
            market,
            equity=share,
            exposure_basis=Decimal(0),
            cash=min(state.cash - state.reserved_cash, share),
            policy_cap=share,
            fee_bps=config.fee_bps,
            risk=whole,
            limit_price=limit,
        )
        if quantity is None:
            decisions.append(_hold(pair, "size_below_minimum_or_no_room"))
            continue
        decisions.append(
            Decision(
                pair=pair,
                action="buy",
                reason="benchmark_entry",
                order=OrderPlan(side="buy", quantity=quantity, limit_price=limit),
            )
        )
    return decisions


def _not_acting(proposal: Proposal | None, unasked: str, ignored: str) -> str:
    """Why the AI strategy leaves a pair alone: not asked, held back, or the wrong direction."""
    if proposal is None:
        return unasked
    return proposal.reason if proposal.action == "hold" else ignored


def _ai_pass(
    *,
    config: LiveConfig,
    now: datetime,
    markets: Mapping[str, Market],
    state: PortfolioState,
    loop: LoopState,
    marks: Mapping[str, Mark | None],
    equity: Decimal | None,
    blocked: str | None,
    proposals: Mapping[str, Proposal],
) -> tuple[list[Decision], list[str]]:
    """The AI strategy's decisions and the pairs whose decision depends on an AI answer.

    Mechanical exits (stop, take-profit, time limit) are the rules' and never wait for or depend on
    the AI; the AI can only add a sell for a position that is not already exiting, or ask for an
    entry. Every size, price and limit still comes from the deterministic code.
    """
    risk = config.risk
    exposure = sum((p.cost_basis for p in state.positions), Decimal(0))
    cash_left = state.cash - state.reserved_cash
    fee_rate = config.fee_bps / Decimal(10000)
    decisions: list[Decision] = []
    needs: list[str] = []
    for pair in config.pairs:
        market = markets.get(pair)
        if (problem := _usable(market, pair)) is not None:
            decisions.append(problem)
            continue
        assert market is not None
        quote, tick = market.quote, market.quote.instrument.tick_size
        position = held_position(state, quote.instrument)
        proposal = proposals.get(pair)
        if position is not None:
            mark = marks.get(pair)
            entry_price = position.cost_basis / position.quantity
            entered = loop.entered_at.get(pair, now - timedelta(hours=risk.max_hold_hours))
            price = mark.price if mark is not None else quote.bids[0].price
            reason: str | None = exit_reason(entry_price, price, entered, now, risk)
            if reason is None:
                needs.append(pair)
                if proposal is None or proposal.action != "sell":
                    kept = _not_acting(proposal, "holding", "ai_buy_ignored_held")
                    decisions.append(
                        Decision(
                            pair=pair,
                            action="hold",
                            reason=kept,
                            detail=proposal.detail if proposal else None,
                        )
                    )
                    continue
                reason = "ai_sell"
            limit = marketable_limit("sell", quote.bids[0].price, tick, risk.limit_buffer_bps)
            decisions.append(
                Decision(
                    pair=pair,
                    action="sell",
                    reason=reason,
                    order=OrderPlan(side="sell", quantity=position.quantity, limit_price=limit),
                    detail=proposal.detail if proposal and reason == "ai_sell" else None,
                )
            )
            continue
        if (
            why := entry_gate(
                blocked=blocked,
                held=False,
                last_entry_at=loop.last_entry_at.get(pair),
                last_stop_at=loop.last_stop_at.get(pair),
                now=now,
                risk=risk,
            )
        ) is not None:
            decisions.append(_hold(pair, why))
            continue
        if equity is None:
            decisions.append(_hold(pair, "equity_unavailable"))
            continue
        limit = marketable_limit("buy", quote.asks[0].price, tick, risk.limit_buffer_bps)
        quantity = _entry_quantity(
            market,
            equity=equity,
            exposure_basis=exposure,
            cash=cash_left,
            policy_cap=config.policy().max_order_notional,
            fee_bps=config.fee_bps,
            risk=risk,
            limit_price=limit,
        )
        if quantity is None:
            # Earlier entries in this cycle can use up the room after the AI was asked. What the
            # AI said still wins the label, and what its call cost stays in the journal.
            if proposal is None or proposal.action == "buy":
                label = "size_below_minimum_or_no_room"
            else:
                label = _not_acting(proposal, "", "ai_sell_ignored_flat")
            decisions.append(
                Decision(
                    pair=pair,
                    action="hold",
                    reason=label,
                    detail=proposal.detail if proposal else None,
                )
            )
            continue
        needs.append(pair)
        if proposal is None or proposal.action != "buy":
            waiting = _not_acting(proposal, "ai_not_reviewed", "ai_sell_ignored_flat")
            decisions.append(
                Decision(
                    pair=pair,
                    action="hold",
                    reason=waiting,
                    detail=proposal.detail if proposal else None,
                )
            )
            continue
        exposure += quantity * limit
        cash_left -= quantity * limit * (1 + fee_rate)
        decisions.append(
            Decision(
                pair=pair,
                action="buy",
                reason="ai_buy",
                order=OrderPlan(side="buy", quantity=quantity, limit_price=limit),
                detail=proposal.detail,
            )
        )
    return decisions, needs


def decide_ai(
    *,
    config: LiveConfig,
    now: datetime,
    markets: Mapping[str, Market],
    state: PortfolioState,
    loop: LoopState,
    marks: Mapping[str, Mark | None],
    equity: Decimal | None,
    blocked: str | None,
    proposals: Mapping[str, Proposal],
) -> list[Decision]:
    return _ai_pass(
        config=config,
        now=now,
        markets=markets,
        state=state,
        loop=loop,
        marks=marks,
        equity=equity,
        blocked=blocked,
        proposals=proposals,
    )[0]


def review_candidates(
    *,
    config: LiveConfig,
    now: datetime,
    interval: timedelta,
    markets: Mapping[str, Market],
    state: PortfolioState,
    loop: LoopState,
    marks: Mapping[str, Mark | None],
    equity: Decimal | None,
    blocked: str | None,
) -> list[str]:
    """Pairs worth paying the AI about now: a decision hangs on the answer and a review is due.

    Nothing is asked about a pair that is already exiting mechanically, is blocked by a risk gate,
    a cooldown or a missing book, or has no room to enter, since the answer could not change what
    the deterministic code does.
    """
    _, needs = _ai_pass(
        config=config,
        now=now,
        markets=markets,
        state=state,
        loop=loop,
        marks=marks,
        equity=equity,
        blocked=blocked,
        proposals={},
    )
    slack = timedelta(minutes=5)  # cycle times jitter by seconds; 4 h must not drift to 4 h 15 min
    due = []
    for pair in needs:
        last = loop.last_review_at.get(pair)
        if last is None or now - last >= interval - slack:
            due.append(pair)
    return due
