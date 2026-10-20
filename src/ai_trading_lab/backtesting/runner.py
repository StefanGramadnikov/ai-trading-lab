"""Sequential virtual-clock proposals routed through the M3 safety engine."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from uuid import uuid5

from ai_trading_lab.backtesting.models import DecisionRecord, Evaluation, ResearchInput
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import OrderIntent, decimal_amount
from ai_trading_lab.strategies.models import Abstain, StrategyFrame, TradeProposal
from ai_trading_lab.strategies.rules import decide


def portfolio_config(spec: ResearchInput) -> dict[str, object]:
    config: dict[str, object] = {
        "research_run_id": str(spec.run_id),
        "cash_unit": spec.cash_unit,
        "cash_step": str(spec.cash_step),
        "initial_cash": str(spec.initial_cash),
        "policy": spec.simulation.model_dump(mode="json"),
    }
    if spec.replay_manifest is not None:
        config["replay_manifest"] = spec.replay_manifest.model_dump(mode="json")
    return config


def evaluate(spec: ResearchInput) -> tuple[Evaluation, History]:
    # Validate again at the public boundary, including trusted-code model_copy users.
    spec = ResearchInput.model_validate(spec.model_dump(mode="json"))
    history = History(uuid5(spec.run_id, "rules-only"), portfolio_config(spec))
    records = [
        execute_decision(spec, history, index, frame, decide(spec.strategy, frame, history.state))
        for index, frame in enumerate(spec.frames)
    ]
    return Evaluation(
        run_id=spec.run_id,
        input_hash=spec.fingerprint,
        dataset_hash=spec.dataset_hash,
        decisions=tuple(records),
        final_state=history.state,
    ), history


def execute_decision(
    spec: ResearchInput,
    history: History,
    index: int,
    frame: StrategyFrame,
    decision: TradeProposal | Abstain,
) -> DecisionRecord:
    """Common deterministic sizing/risk route for approved research proposals."""
    with localcontext() as context:
        context.prec = 100
        receipt = None
        reason = None
        if isinstance(decision, TradeProposal):
            instrument = frame.quote.instrument
            # Round inside the proposed limit, never authorize a worse price.
            rounding = ROUND_FLOOR if decision.side == "buy" else ROUND_CEILING
            limit = (decision.limit_price / instrument.tick_size).to_integral_value(
                rounding=rounding
            ) * instrument.tick_size
            held = next(
                (p.quantity for p in history.state.positions if p.instrument == instrument),
                Decimal(0),
            )
            if limit <= 0:
                reason = "price_below_tick"
            else:
                quantity = spec.order_notional / limit if decision.side == "buy" else held
                quantity = (quantity / instrument.quantity_step).to_integral_value(
                    rounding=ROUND_FLOOR
                ) * instrument.quantity_step
                if quantity <= 0:
                    reason = "quantity_below_step_or_no_position"
                else:
                    try:
                        quantity = decimal_amount(quantity)
                    except ValueError:
                        reason = "quantity_out_of_bounds"
                    if reason is None:
                        key = f"decision:{index}"
                        intent = OrderIntent(
                            portfolio_id=history.state.portfolio_id,
                            idempotency_key=key,
                            instrument=instrument,
                            side=decision.side,
                            quantity=quantity,
                            limit_price=limit,
                            created_at_utc=frame.at_utc,
                            expires_at_utc=frame.at_utc + timedelta(seconds=spec.order_ttl_seconds),
                        )
                        effect = history.apply(
                            "order",
                            key,
                            {
                                "intent": intent.model_dump(mode="json"),
                                "quote": frame.quote.model_dump(mode="json"),
                            },
                            frame.at_utc,
                        )
                        receipt = effect.receipt
    return DecisionRecord(
        index=index,
        at_utc=frame.at_utc,
        snapshot_id=frame.quote.snapshot_id,
        decision=decision,
        sizing_reason=reason,
        receipt=receipt,
    )
