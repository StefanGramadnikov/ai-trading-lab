"""Deterministic research summaries; modeled marks never realize open positions."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, localcontext

from ai_trading_lab.backtesting.models import Evaluation, ResearchInput
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import ExecutionQuote, OrderIntent
from ai_trading_lab.paper_execution.simulation import simulate


def report(spec: ResearchInput, evaluation: Evaluation, history: History) -> dict[str, object]:
    if (
        evaluation.run_id != spec.run_id
        or evaluation.input_hash != spec.fingerprint
        or evaluation.dataset_hash != spec.dataset_hash
        or evaluation.final_state != history.state
        or history.policy != spec.simulation
    ):
        raise ValueError("report_input_mismatch")
    with localcontext() as context:
        context.prec = 100
        return _report(spec, evaluation, history)


def _report(spec: ResearchInput, evaluation: Evaluation, history: History) -> dict[str, object]:
    state = evaluation.final_state
    latest: dict[str, ExecutionQuote] = {}
    for frame in spec.frames:
        if frame.at_utc <= spec.end_at_utc and frame.quote.received_at_utc <= spec.end_at_utc:
            latest[frame.quote.instrument.key] = frame.quote
    marks: list[dict[str, object]] = []
    total_mark = Decimal(0)
    mark_complete = True
    # Complete liquidation is valued independently without mutating cash, positions or depth.
    # Starting before the endpoint retains the configured latency and forbids future quotes.
    mark_start = spec.end_at_utc - timedelta(milliseconds=spec.simulation.latency_ms)
    for position in state.positions:
        instrument = position.instrument
        quote = latest.get(instrument.key)
        value: Decimal | None = None
        reason = "missing_quote"
        if (
            quote is not None
            and quote.event_time_utc is not None
            and quote.event_time_utc > mark_start
        ):
            reason = "future_mark_evidence"
        elif quote is not None:
            previous = history.quotes.get(quote.snapshot_id)
            consumed = previous[2] if previous is not None else ()
            intent = OrderIntent(
                portfolio_id=state.portfolio_id,
                idempotency_key="report-liquidation:" + instrument.key,
                instrument=instrument,
                side="sell",
                quantity=position.quantity,
                limit_price=instrument.tick_size,
                created_at_utc=mark_start,
                expires_at_utc=spec.end_at_utc + timedelta(seconds=1),
            )
            result = simulate(intent, quote, spec.simulation, mark_start, consumed)
            reason = result.reason
            if result.status == "FILLED" and result.quantity == position.quantity:
                value = result.notional - result.fee
        if value is None:
            mark_complete = False
        else:
            total_mark += value
        marks.append(
            {
                "instrument": instrument.model_dump(mode="json"),
                "quantity": str(position.quantity),
                "cost_basis": str(position.cost_basis),
                "modeled_liquidation_value": str(value) if value is not None else None,
                "mark_reason": reason,
                "snapshot_id": str(quote.snapshot_id) if quote else None,
            }
        )
    nav = state.cash + total_mark if mark_complete else None
    receipts = [record.receipt for record in evaluation.decisions if record.receipt is not None]
    counts = {
        "decisions": len(evaluation.decisions),
        "abstentions": sum(record.decision.kind == "abstain" for record in evaluation.decisions),
        "proposals": sum(record.decision.kind == "proposal" for record in evaluation.decisions),
        "sizing_refusals": sum(record.sizing_reason is not None for record in evaluation.decisions),
        "orders": len(receipts),
        "fills": sum(receipt.result.quantity > 0 for receipt in receipts),
        "partial_fills": sum(receipt.result.status == "PARTIALLY_FILLED" for receipt in receipts),
        "rejections": sum(receipt.result.status == "REJECTED" for receipt in receipts),
        "expiries": sum(receipt.result.status == "EXPIRED" for receipt in receipts),
    }
    return {
        "schema_version": "1",
        "run_id": str(spec.run_id),
        "input_hash": evaluation.input_hash,
        "dataset_hash": evaluation.dataset_hash,
        "code_version": spec.code_version,
        "engine_hash": evaluation.engine_hash,
        "data_mode": spec.data_mode.value,
        "evidence_label": "synthetic_fixture"
        if spec.data_mode.value == "FIXTURE"
        else "archived_replay",
        "strategy": spec.strategy.model_dump(mode="json"),
        "simulation": spec.simulation.model_dump(mode="json"),
        "start_at_utc": spec.start_at_utc.isoformat(),
        "end_at_utc": spec.end_at_utc.isoformat(),
        "cash_unit": state.cash_unit,
        "initial_cash": str(state.initial_cash),
        "cash": str(state.cash),
        "realized_gross_pnl": str(state.realized_gross),
        "fees": str(state.fees),
        "realized_gross_less_all_paid_fees": str(state.realized_gross - state.fees),
        "open_positions": marks,
        "nav": str(nav) if nav is not None else None,
        "roi": str((nav - state.initial_cash) / state.initial_cash) if nav is not None else None,
        "mark_basis": "modeled_full_liquidation_after_execution_costs",
        "cash_benchmark": {
            "initial_cash": str(state.initial_cash),
            "nav": str(state.initial_cash),
            "roi": "0",
            "fees": "0",
        },
        "counts": counts,
        "decisions": [record.model_dump(mode="json") for record in evaluation.decisions],
        "operating_costs": None,
        "profit_after_all_expenses": None,
        "limitations": [
            "Simulated execution; no demonstrated investment edge",
            "Operating expenses are unmeasured; no all-expenses profit claim",
            "Open prediction contracts are not settled by a modeled mark",
        ],
    }
