"""Synthetic tapes verify reporting arithmetic, never empirical performance."""

import json
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from ai_trading_lab.backtesting.models import DecisionRecord, Evaluation, ResearchInput
from ai_trading_lab.backtesting.reporting import report
from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import OrderIntent
from ai_trading_lab.strategies.models import Abstain, TradeProposal

FIXTURE = Path(__file__).parents[1] / "fixtures" / "paper_scenario.json"


def spec_sample() -> ResearchInput:
    data = json.loads(FIXTURE.read_text())
    operation = data["operations"][0]
    return ResearchInput.model_validate(
        {
            "run_id": data["portfolio_id"],
            "code_version": "a" * 40,
            "strategy": {"kind": "cash", "version": "synthetic-only-v1"},
            "simulation": data["policy"],
            "cash_unit": "USD",
            "cash_step": "0.01",
            "initial_cash": "1000",
            "order_notional": "10",
            "order_ttl_seconds": 60,
            "start_at_utc": operation["at"],
            "end_at_utc": operation["at"],
            "data_mode": "FIXTURE",
            "frames": [{"at_utc": operation["at"], "quote": operation["quote"]}],
        }
    )


def history_for(spec: ResearchInput, *, buy: bool = True) -> tuple[History, Evaluation]:
    history = History(
        spec.run_id,
        {
            "cash_unit": spec.cash_unit,
            "cash_step": str(spec.cash_step),
            "initial_cash": str(spec.initial_cash),
            "policy": spec.simulation.model_dump(mode="json"),
        },
    )
    frame = spec.frames[0]
    decision = DecisionRecord(
        index=0,
        at_utc=frame.at_utc,
        snapshot_id=frame.quote.snapshot_id,
        decision=Abstain(reason="cash"),
    )
    if buy:
        intent = OrderIntent(
            portfolio_id=spec.run_id,
            idempotency_key="synthetic-buy",
            instrument=frame.quote.instrument,
            side="buy",
            quantity=Decimal(1),
            limit_price=Decimal("10.10")
            if frame.quote.instrument.kind == "spot"
            else Decimal("0.6"),
            created_at_utc=frame.at_utc,
            expires_at_utc=frame.at_utc + timedelta(seconds=60),
        )
        effect = history.apply(
            "order",
            intent.idempotency_key,
            {
                "intent": intent.model_dump(mode="json"),
                "quote": frame.quote.model_dump(mode="json"),
            },
            frame.at_utc,
        )
        decision = DecisionRecord(
            index=0,
            at_utc=frame.at_utc,
            snapshot_id=frame.quote.snapshot_id,
            decision=TradeProposal(
                side="buy", limit_price=intent.limit_price, reason="synthetic", evidence=()
            ),
            receipt=effect.receipt,
        )
    evaluation = Evaluation(
        run_id=spec.run_id,
        input_hash=spec.fingerprint,
        dataset_hash=spec.dataset_hash,
        decisions=(decision,),
        final_state=history.state,
    )
    return history, evaluation


def evaluate_report(spec: ResearchInput, *, buy: bool = True) -> dict[str, object]:
    history, evaluation = history_for(spec, buy=buy)
    return report(spec, evaluation, history)


def test_cash_benchmark_and_fixture_labels() -> None:
    output = evaluate_report(spec_sample(), buy=False)
    assert output["nav"] == "1000"
    assert output["roi"] == "0"
    assert output["evidence_label"] == "synthetic_fixture"
    assert output["profit_after_all_expenses"] is None
    assert output["operating_costs"] is None
    assert output["counts"] == {
        "decisions": 1,
        "abstentions": 1,
        "proposals": 0,
        "sizing_refusals": 0,
        "orders": 0,
        "fills": 0,
        "partial_fills": 0,
        "rejections": 0,
        "expiries": 0,
    }
    json.dumps(output)


def test_modeled_mark_accounts_for_entry_exit_fees_once_without_mutating() -> None:
    spec = spec_sample()
    history, evaluation = history_for(spec)
    before = (history.state, dict(history.quotes), dict(history.effects))
    output = report(spec, evaluation, history)
    assert output["cash"] == "989.97"
    assert output["fees"] == "0.02"
    assert output["realized_gross_pnl"] == "0"
    assert output["nav"] == "999.95"
    assert output["roi"] == "-0.00005"
    assert output["realized_gross_less_all_paid_fees"] == "-0.02"
    assert (history.state, history.quotes, history.effects) == before
    assert report(spec, evaluation, history) == output
    with localcontext() as context:
        context.prec = 4
        assert report(spec, evaluation, history) == output


@pytest.mark.parametrize("failure", ["stale", "partial", "exhausted", "future_receipt", "missing"])
def test_open_unpriced_or_unfillable_holdings_produce_null_nav(failure: str) -> None:
    original = spec_sample()
    history, evaluation = history_for(original)
    quote = original.frames[0].quote
    at = original.end_at_utc
    if failure == "stale":
        at += timedelta(seconds=61)
    elif failure == "partial":
        quote = quote.model_copy(update={"quality": DataQuality.PARTIAL})
    elif failure == "exhausted":
        history.quotes[quote.snapshot_id] = (quote, (Decimal(2),), (Decimal(2),))
    elif failure == "future_receipt":
        quote = quote.model_copy(update={"received_at_utc": at + timedelta(seconds=1)})
    else:
        quote = quote.model_copy(
            update={"instrument": quote.instrument.model_copy(update={"native_id": "other"})}
        )
    spec = original.model_copy(
        update={
            "end_at_utc": at,
            "frames": (original.frames[0].model_copy(update={"quote": quote}),),
        }
    )
    evaluation = evaluation.model_copy(
        update={"input_hash": spec.fingerprint, "dataset_hash": spec.dataset_hash}
    )
    output = report(spec, evaluation, history)
    assert output["nav"] is None and output["roi"] is None
    assert output["realized_gross_pnl"] == "0"


def test_latency_retained_when_marking_end_window() -> None:
    spec = spec_sample()
    spec = spec.model_copy(
        update={
            "simulation": spec.simulation.model_copy(update={"latency_ms": 1000}),
            "end_at_utc": spec.end_at_utc + timedelta(seconds=1),
        }
    )
    assert evaluate_report(spec)["nav"] == "999.95"


def test_prediction_mark_does_not_settle_unresolved_contract() -> None:
    data = spec_sample().model_dump(mode="json")
    quote = data["frames"][0]["quote"]
    quote["instrument"].update(kind="prediction", market_id="synthetic-only", outcome="yes")
    quote.update(
        spot_price=None,
        spot_capacity=None,
        impact_bps=None,
        bids=[{"price": "0.4", "quantity": "10"}],
        asks=[{"price": "0.5", "quantity": "10"}],
    )
    data["simulation"].update(fee_bps="0", max_cost_bps="10000")
    output = evaluate_report(ResearchInput.model_validate(data))
    assert output["nav"] == "999.9"
    assert output["realized_gross_pnl"] == "0"
    positions = output["open_positions"]
    assert isinstance(positions, list) and len(positions) == 1
    assert positions[0]["quantity"] == "1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "00000000-0000-4000-8000-000000000099"),
        ("input_hash", "wrong"),
        ("dataset_hash", "wrong"),
    ],
)
def test_cross_run_report_refused(field: str, value: object) -> None:
    spec = spec_sample()
    history, evaluation = history_for(spec)
    with pytest.raises(ValueError, match="mismatch"):
        report(spec, evaluation.model_copy(update={field: value}), history)


def test_future_event_cannot_become_mark_even_with_permitted_clock_skew() -> None:
    original = spec_sample()
    history, evaluation = history_for(original)
    quote = original.frames[0].quote.model_copy(
        update={"event_time_utc": original.end_at_utc + timedelta(seconds=1)}
    )
    spec = original.model_copy(
        update={
            "simulation": original.simulation.model_copy(update={"future_skew_seconds": 5}),
            "frames": (original.frames[0].model_copy(update={"quote": quote}),),
        }
    )
    history.policy = spec.simulation
    evaluation = evaluation.model_copy(
        update={"input_hash": spec.fingerprint, "dataset_hash": spec.dataset_hash}
    )
    output = report(spec, evaluation, history)
    assert output["nav"] is None
    positions = output["open_positions"]
    assert isinstance(positions, list)
    assert positions[0]["mark_reason"] == "future_mark_evidence"


def test_closed_position_reports_realized_gross_and_cash_without_double_fees() -> None:
    spec = spec_sample()
    history, evaluation = history_for(spec)
    frame = spec.frames[0]
    intent = OrderIntent(
        portfolio_id=spec.run_id,
        idempotency_key="synthetic-sell",
        instrument=frame.quote.instrument,
        side="sell",
        quantity=Decimal(1),
        limit_price=Decimal("0.01"),
        created_at_utc=frame.at_utc,
        expires_at_utc=frame.at_utc + timedelta(seconds=60),
    )
    effect = history.apply(
        "order",
        intent.idempotency_key,
        {"intent": intent.model_dump(mode="json"), "quote": frame.quote.model_dump(mode="json")},
        frame.at_utc,
    )
    record = DecisionRecord(
        index=1,
        at_utc=frame.at_utc,
        snapshot_id=frame.quote.snapshot_id,
        decision=TradeProposal(
            side="sell", limit_price=intent.limit_price, reason="synthetic", evidence=()
        ),
        receipt=effect.receipt,
    )
    evaluation = evaluation.model_copy(
        update={"final_state": history.state, "decisions": (*evaluation.decisions, record)}
    )
    output = report(spec, evaluation, history)
    assert output["nav"] == "999.95"
    assert output["cash"] == "999.95"
    assert output["realized_gross_pnl"] == "-0.02"
    assert output["fees"] == "0.03"
    assert output["realized_gross_less_all_paid_fees"] == "-0.05"
    assert output["open_positions"] == []


def test_rejected_orders_and_sizing_refusals_remain_visible() -> None:
    spec = spec_sample()
    spec = spec.model_copy(
        update={"simulation": spec.simulation.model_copy(update={"max_order_notional": Decimal(1)})}
    )
    history, evaluation = history_for(spec)
    refusal = evaluation.decisions[0].model_copy(
        update={"index": 1, "receipt": None, "sizing_reason": "rounded_quantity_zero"}
    )
    evaluation = evaluation.model_copy(update={"decisions": (*evaluation.decisions, refusal)})
    output = report(spec, evaluation, history)
    counts = output["counts"]
    assert isinstance(counts, dict)
    assert counts["rejections"] == 1
    assert counts["sizing_refusals"] == 1
    assert counts["orders"] == 1 and counts["fills"] == 0
    assert output["nav"] == "1000"
