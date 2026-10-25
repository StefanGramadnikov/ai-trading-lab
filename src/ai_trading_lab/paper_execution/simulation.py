"""Pure, bounded IOC simulation with adverse fixed-point rounding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException, localcontext
from typing import Literal

from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    SimulationPolicy,
    SimulationResult,
    decimal_amount,
)

ZERO = Decimal(0)
BPS = Decimal(10000)


def _round(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    return (value / step).to_integral_value(rounding=rounding) * step


def simulate(
    intent: OrderIntent,
    quote: ExecutionQuote,
    policy: SimulationPolicy,
    now: datetime,
    consumed: tuple[Decimal, ...] = (),
) -> SimulationResult:
    """Return a final IOC outcome; consumed liquidity belongs to this quote's side."""
    timestamp = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    try:
        timestamp += timedelta(milliseconds=policy.latency_ms)
        with localcontext() as context:
            context.prec = 100
            return _simulate(intent, quote, policy, now, timestamp, consumed)
    except (DecimalException, ValueError, OverflowError, TypeError):
        return SimulationResult(
            status="REJECTED",
            reason="invalid_numeric_or_time_input",
            quantity=ZERO,
            notional=ZERO,
            fee=ZERO,
            consumed=(),
            executed_at_utc=timestamp,
        )


def _simulate(
    intent: OrderIntent,
    quote: ExecutionQuote,
    policy: SimulationPolicy,
    now: datetime,
    executed: datetime,
    consumed: tuple[Decimal, ...],
) -> SimulationResult:
    if len(consumed) > 100:
        raise ValueError("consumed_shape")
    used = tuple(decimal_amount(amount) for amount in consumed)
    if any(amount < 0 for amount in used):
        raise ValueError("negative_consumption")

    original_consumed = used

    def result(
        reason: str,
        status: Literal["CANCELLED", "REJECTED", "EXPIRED"] = "REJECTED",
    ) -> SimulationResult:
        return SimulationResult(
            status=status,
            reason=reason,
            quantity=ZERO,
            notional=ZERO,
            fee=ZERO,
            consumed=original_consumed,
            executed_at_utc=executed,
        )

    if now.tzinfo is None or now.utcoffset() is None:
        return result("naive_clock")
    if intent.created_at_utc > now:
        return result("intent_in_future")
    if intent.expires_at_utc <= executed:
        return result("order_expired", "EXPIRED")
    if intent.instrument.key != quote.instrument.key:
        return result("instrument_mismatch")
    if quote.quality != DataQuality.VALID:
        return result("quote_quality")
    if quote.event_time_utc is None:
        return result("event_time_unknown")
    if quote.received_at_utc > now:
        return result("quote_unavailable")
    skew = timedelta(seconds=policy.future_skew_seconds)
    age = timedelta(seconds=policy.max_age_seconds)
    for observed in (quote.event_time_utc, quote.received_at_utc):
        if observed > executed + skew:
            return result("quote_in_future")
        if executed - observed > age:
            return result("quote_stale")
    if quote.event_time_utc > quote.received_at_utc + skew:
        return result("event_after_receipt")
    instrument = intent.instrument
    if intent.quantity % instrument.quantity_step:
        return result("quantity_step")
    if intent.limit_price % instrument.tick_size:
        return result("price_tick")
    if intent.side == "buy" and intent.quantity * intent.limit_price > policy.max_order_notional:
        return result("max_order_notional")

    buy = intent.side == "buy"
    levels: tuple[tuple[Decimal, Decimal], ...]
    reference: Decimal
    if instrument.kind == "spot":
        if quote.spot_price is None or quote.spot_capacity is None or quote.impact_bps is None:
            return result("spot_assumptions_missing")
        if quote.bids or quote.asks:
            return result("ambiguous_quote")
        impact = quote.impact_bps / BPS
        price = quote.spot_price * (1 + impact if buy else 1 - impact)
        levels = ((price, quote.spot_capacity),)
        reference = quote.spot_price
    else:
        if quote.spot_price is not None or quote.spot_capacity is not None:
            return result("ambiguous_quote")
        if quote.impact_bps not in (None, ZERO):
            return result("book_impact_not_supported")
        if any(level.derived for level in (*quote.bids, *quote.asks)):
            return result("derived_depth")
        if any(a.price < b.price for a, b in zip(quote.bids, quote.bids[1:], strict=False)):
            return result("unordered_book")
        if any(a.price > b.price for a, b in zip(quote.asks, quote.asks[1:], strict=False)):
            return result("unordered_book")
        if not quote.bids or not quote.asks:
            return result("cost_reference_missing")
        bid, ask = quote.bids[0].price, quote.asks[0].price
        if bid > ask or bid + ask == 0:
            return result("invalid_spread")
        reference = (bid + ask) / 2
        levels = tuple(
            (level.price, level.quantity) for level in (quote.asks if buy else quote.bids)
        )
    if used and len(used) != len(levels):
        return result("consumed_shape")
    if not used:
        used = tuple(ZERO for _ in levels)
    if any(amount > level[1] for amount, level in zip(used, levels, strict=True)):
        return result("consumed_exceeds_depth")
    updated = list(used)
    quantity = notional = fee = ZERO
    for index, (raw_price, available) in enumerate(levels):
        price = _round(raw_price, instrument.tick_size, up=buy)
        if price <= 0 or (instrument.kind == "prediction" and price > 1):
            continue
        if (buy and price > intent.limit_price) or (not buy and price < intent.limit_price):
            continue
        fill_quantity = _round(
            min(intent.quantity - quantity, available - used[index]),
            instrument.quantity_step,
            up=False,
        )
        if not fill_quantity:
            continue
        fill_notional = _round(fill_quantity * price, instrument.cash_step, up=buy)
        if not fill_notional:
            continue
        # Cash-unit rounding must still honor the caller's effective limit.
        limit_notional = fill_quantity * intent.limit_price
        if (buy and fill_notional > limit_notional) or (not buy and fill_notional < limit_notional):
            continue
        quantity += fill_quantity
        notional += fill_notional
        updated[index] += fill_quantity
        if quantity == intent.quantity:
            break
    if not quantity:
        return result("no_executable_liquidity", "CANCELLED")
    if buy and notional > policy.max_order_notional:
        return result("max_order_notional")
    fee = _round(notional * policy.fee_bps / BPS, instrument.cash_step, up=True)
    # Effective cash price includes depth, impact and adverse tick/cash rounding.
    # Compare with the contemporaneous midpoint (or explicit pre-impact spot price).
    # Model the same adverse friction and rounded fee on each round-trip leg.
    effective_price = notional / quantity
    adverse = max(ZERO, effective_price - reference if buy else reference - effective_price)
    cost = 2 * BPS * (adverse / reference + fee / notional)
    if cost > policy.max_cost_bps:
        return result("max_cost_bps")
    return SimulationResult(
        status="FILLED" if quantity == intent.quantity else "PARTIALLY_FILLED",
        reason="filled" if quantity == intent.quantity else "ioc_remainder_cancelled",
        quantity=quantity,
        notional=notional,
        fee=fee,
        consumed=tuple(updated),
        executed_at_utc=executed,
    )
