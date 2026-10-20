"""Full-tape deterministic reducer for one persistent paper portfolio."""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field

from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.history import History, clock
from ai_trading_lab.paper_execution.models import ExecutionQuote, OrderIntent, SimulationPolicy
from ai_trading_lab.paper_execution.replay import ReplayEntry
from ai_trading_lab.paper_execution.simulation import simulate
from ai_trading_lab.runtime.evidence import native_event_id, observed_quote
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.strategies.models import Abstain, StrategyFrame, TradeProposal
from ai_trading_lab.strategies.rules import decide


class _Event(FrozenModel):
    key: str = Field(min_length=1, max_length=200)
    at: AwareDatetime
    payload: dict[str, object]


def _policy(config: RuntimeConfig) -> SimulationPolicy:
    policy = config.simulation
    return SimulationPolicy.model_validate(
        {
            **policy.model_dump(mode="json"),
            "max_order_notional": min(
                policy.max_order_notional, config.initial_cash * config.profile.max_order_fraction
            ),
            "max_position_notional": min(
                policy.max_position_notional,
                config.initial_cash * config.profile.max_position_fraction,
            ),
            "max_total_exposure": min(
                policy.max_total_exposure,
                config.initial_cash * config.profile.max_exposure_fraction,
            ),
        }
    )


def _nav(
    history: History, marks: dict[str, ExecutionQuote], at: datetime, config: RuntimeConfig
) -> Decimal | None:
    value = history.state.cash
    for position in history.state.positions:
        instrument = position.instrument
        quote = marks.get(instrument.key)
        if quote is None:
            return None
        policy = history.policy
        intent = OrderIntent(
            portfolio_id=history.state.portfolio_id,
            idempotency_key="runtime-mark",
            instrument=instrument,
            side="sell",
            quantity=position.quantity,
            limit_price=instrument.tick_size,
            created_at_utc=at,
            expires_at_utc=at + timedelta(seconds=config.order_ttl_seconds),
        )
        previous = history.quotes.get(quote.snapshot_id)
        used = previous[2] if previous else ()
        # simulate adds latency; at already is the valuation/execution clock.
        policy = policy.model_copy(update={"latency_ms": 0})
        result = simulate(intent, quote, policy, at, used)
        if result.status != "FILLED" or result.quantity != position.quantity:
            return None
        value += result.notional - result.fee
    return value


def _execute(
    config: RuntimeConfig,
    history: History,
    frame: StrategyFrame,
    decision: TradeProposal | Abstain,
    index: int,
    entry: ReplayEntry | None,
) -> dict[str, object]:
    output: dict[str, object] = {
        "decision": decision.model_dump(mode="json"),
        "receipt": None,
        "sizing_reason": None,
    }
    if isinstance(decision, Abstain):
        return output
    instrument = frame.quote.instrument
    rounding = ROUND_FLOOR if decision.side == "buy" else ROUND_CEILING
    limit = (decision.limit_price / instrument.tick_size).to_integral_value(
        rounding=rounding
    ) * instrument.tick_size
    held = next(
        (p.quantity for p in history.state.positions if p.instrument == instrument), Decimal(0)
    )
    if limit <= 0:
        output["sizing_reason"] = "price_below_tick"
        return output
    quantity = (
        min(config.order_notional, history.policy.max_order_notional) / limit
        if decision.side == "buy"
        else held
    )
    quantity = (quantity / instrument.quantity_step).to_integral_value(
        rounding=ROUND_FLOOR
    ) * instrument.quantity_step
    if quantity <= 0:
        output["sizing_reason"] = "quantity_below_step_or_no_position"
        return output
    key = f"runtime-decision:{index}"
    intent = OrderIntent(
        portfolio_id=history.state.portfolio_id,
        idempotency_key=key,
        instrument=instrument,
        side=decision.side,
        quantity=quantity,
        limit_price=limit,
        created_at_utc=frame.at_utc,
        expires_at_utc=frame.at_utc + timedelta(seconds=config.order_ttl_seconds),
    )
    payload: dict[str, object] = {
        "intent": intent.model_dump(mode="json"),
        "quote": frame.quote.model_dump(mode="json"),
    }
    if entry is not None:
        payload["source_entry"] = entry.model_dump(mode="json")
    effect = history.apply("order", key, payload, frame.at_utc)
    assert effect.receipt is not None
    output["receipt"] = effect.receipt.model_dump(mode="json")
    return output


def _frame(
    config: RuntimeConfig, payload: dict[str, object], at: datetime
) -> tuple[StrategyFrame, ReplayEntry | None]:
    frame = StrategyFrame.model_validate(payload.get("frame"))
    if frame.at_utc != at or frame.quote.data_mode.value != config.data_mode:
        raise ValueError("runtime_frame_clock_or_mode_mismatch")
    entry = None
    if config.data_mode == "OBSERVED":
        entry = ReplayEntry.model_validate(payload.get("source_entry"))
        if (
            frame.quote != observed_quote(entry)
            or frame.quote.instrument != config.instrument
            or frame.resolution_terms_hash != entry.resolution_terms_hash
        ):
            raise ValueError("runtime_raw_quote_mismatch")
    elif payload.get("source_entry") is not None:
        raise ValueError("runtime_fixture_cannot_claim_observed_capture")
    return frame, entry


def _decision(
    config: RuntimeConfig, frame: StrategyFrame, history: History
) -> TradeProposal | Abstain:
    if config.data_mode == "OBSERVED":
        # M4 is explicitly an offline rules engine. This ephemeral REPLAY view is
        # derived from qualified originals; orders/evidence keep OBSERVED labels.
        data = frame.model_dump(mode="json")
        data["quote"]["data_mode"] = "REPLAY"
        for name in ("forecast", "momentum"):
            if data[name] is not None:
                if data[name]["data_mode"] != "OBSERVED":
                    return Abstain(reason="runtime_evidence_mode_mismatch")
                data[name]["data_mode"] = "REPLAY"
        for point in data["history"]:
            if point["data_mode"] != "OBSERVED":
                return Abstain(reason="runtime_evidence_mode_mismatch")
            point["data_mode"] = "REPLAY"
        frame = StrategyFrame.model_validate(data)
    return decide(config.strategy, frame, history.state)


def reducer(config: dict[str, object], events: tuple[dict[str, object], ...]) -> dict[str, object]:
    """Rebuild from original funding; reject corrupt tape rather than repairing it."""
    with localcontext() as context:
        context.prec = 100
        return _reduce(RuntimeConfig.model_validate(config), events)


def _reduce(config: RuntimeConfig, events: tuple[dict[str, object], ...]) -> dict[str, object]:
    history = History(
        uuid5(config.session_id, "runtime-portfolio"),
        {
            "runtime_session_id": str(config.session_id),
            "runtime_data_mode": config.data_mode,
            "cash_unit": config.cash_unit,
            "cash_step": str(config.cash_step),
            "initial_cash": str(config.initial_cash),
            "policy": _policy(config).model_dump(mode="json"),
        },
    )
    last_at = config.started_at_utc
    day = last_at.date()
    day_open = Decimal(0)
    peak = config.initial_cash
    nav: Decimal | None = config.initial_cash
    risk: str | None = None
    control: str | None = None
    stopped = False
    cycles = attempts = 0
    pending = False
    marks: dict[str, ExecutionQuote] = {}
    seen: dict[str, str] = {}
    keys: set[str] = set()
    last_decision: dict[str, object] | None = None

    def update_risk(at: datetime) -> None:
        nonlocal day, day_open, peak, nav, risk
        if at.date() != day:
            day, day_open = at.date(), history.state.realized_gross - history.state.fees
        nav = _nav(history, marks, at, config)
        if nav is not None:
            peak = max(peak, nav)
        if risk is None:
            if day_open - (history.state.realized_gross - history.state.fees) >= (
                config.initial_cash * config.profile.daily_loss_fraction
            ):
                risk = "daily_loss_limit"
            elif nav is not None and peak - nav >= peak * config.profile.drawdown_fraction:
                risk = "drawdown_limit"

    for raw in events:
        event = _Event.model_validate(raw)
        at = clock(event.at)
        if event.key in keys or at < last_at or (history.last_at and at < history.last_at):
            raise ValueError("runtime_duplicate_key_or_clock_conflict")
        keys.add(event.key)
        action = event.payload.get("action")
        allowed = (
            {"action", "frame", "source_entry"}
            if action == "tick"
            else {"action", "reason", "poll_id"}
        )
        if set(event.payload) - allowed:
            raise ValueError("runtime_unknown_payload_fields")
        if action not in {"tick", "poll", "pause", "resume", "stop", "failure"}:
            raise ValueError("runtime_unknown_action")
        if stopped and action != "stop":
            raise ValueError("runtime_session_stopped")
        if at - last_at > timedelta(seconds=config.max_gap_seconds):
            control = "clock_gap"
        last_at = at
        if at >= config.deadline_at_utc:
            stopped = True
            pending = False
            update_risk(at)
            continue
        if action == "stop":
            stopped, pending = True, False
        elif action == "pause":
            control = "manual_pause"
        elif action == "resume":
            control = None
        elif action == "poll":
            if pending or attempts >= config.max_cycles:
                raise ValueError("runtime_poll_admission_refused")
            attempts += 1
            pending = True
        elif action == "failure":
            pending = False
            control = "collection_failure"
            if attempts >= config.max_cycles:
                stopped = True
        elif action == "tick":
            if cycles >= config.max_cycles:
                raise ValueError("runtime_cycle_capacity")
            pending = False
            frame, entry = _frame(config, event.payload, at)
            cycles += 1
            native = native_event_id(frame.quote)
            signature = content_hash(
                frame.quote.model_dump(mode="json", exclude={"snapshot_id", "received_at_utc"})
            )
            duplicate = native in seen
            if duplicate and seen[native] != signature:
                raise ValueError("runtime_native_event_conflict")
            if not duplicate:
                seen[native] = signature
                frame = frame.model_copy(
                    update={"quote": frame.quote.model_copy(update={"snapshot_id": UUID(native)})}
                )
                marks[frame.quote.instrument.key] = frame.quote
            valuation_at = at + timedelta(milliseconds=history.policy.latency_ms)
            update_risk(valuation_at)
            decision = _decision(config, frame, history)
            block = risk or control or ("unavailable_nav" if nav is None else None)
            if duplicate:
                decision = Abstain(reason="duplicate_native_event")
            elif isinstance(decision, TradeProposal) and decision.side == "buy" and block:
                decision = Abstain(reason=block)
            if len(history.effects) >= 1000:
                stopped = True
                decision = Abstain(reason="runtime_history_capacity")
            if valuation_at >= config.deadline_at_utc:
                stopped = True
                decision = Abstain(reason="runtime_deadline")
            last_decision = _execute(config, history, frame, decision, cycles, entry)
            update_risk(valuation_at)
            if cycles >= config.max_cycles or attempts >= config.max_cycles:
                stopped = True
        else:
            raise AssertionError("validated action")
        if action != "tick":
            update_risk(at)
    reason = risk or control or ("unavailable_nav" if nav is None else None)
    status = (
        "STOPPED"
        if stopped
        else "PAUSED"
        if control == "manual_pause"
        else "DEGRADED"
        if control
        else "RUNNING"
    )
    commands = [
        {
            "kind": effect.kind,
            "key": effect.key,
            "at": effect.submitted_at.isoformat(),
            "payload": effect.payload,
        }
        for effect in history.effects.values()
    ]
    return {
        "status": status,
        "portfolio": history.state.model_dump(mode="json"),
        "nav": str(nav) if nav is not None else None,
        "entry_suspended": stopped or reason is not None,
        "suspension_reason": reason,
        "cycles": cycles,
        "poll_attempts": attempts,
        "poll_pending": pending,
        "last_at": last_at.isoformat(),
        "last_decision": last_decision,
        "peak_nav": str(peak),
        "day": day.isoformat(),
        "day_open_realized_net": str(day_open),
        "risk_latch": risk,
        "history": commands,
        "history_hash": content_hash(commands),
        "native_events": sorted(seen),
        "paper_only": True,
        "ai_enabled": False,
    }
