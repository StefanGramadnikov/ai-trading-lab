"""Synthetic valuation and sampled-path arithmetic, never empirical trading evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.backtesting.runner import evaluate, execute_decision, portfolio_config
from ai_trading_lab.experiments.metrics import MetricPoint, mark_point, summarize
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.strategies.rules import decide
from tests.unit.test_backtest_reporting import history_for, spec_sample

AT = datetime(2026, 10, 20, tzinfo=UTC)


def point(nav: str | None, seconds: int = 0, **extra: object) -> MetricPoint:
    return MetricPoint.model_validate(
        {
            "at_utc": AT + timedelta(seconds=seconds),
            "cash_unit": "USD",
            "nav": nav,
            "cash": "50",
            "realized_gross": "0",
            "fees": "0",
            "exposure_cost_basis": "50",
            "open_positions": 1,
            "marks_complete": nav is not None,
            **extra,
        }
    )


def test_mark_reuses_full_liquidation_costs_without_realizing_open_positions() -> None:
    spec = spec_sample()
    history, _ = history_for(spec)
    before = (history.state, dict(history.effects), dict(history.quotes))
    result = mark_point(spec, history, spec.frames, spec.end_at_utc)
    assert result.nav == Decimal("999.95")
    assert result.realized_gross == 0
    assert result.fees == Decimal("0.02")
    assert result.exposure_cost_basis == Decimal("10.01")
    assert result.open_positions == 1
    assert result.marks_complete
    assert (history.state, history.effects, history.quotes) == before
    with localcontext() as context:
        context.prec = 3
        assert mark_point(spec, history, spec.frames, spec.end_at_utc) == result
    summary = summarize((result,), (), spec.initial_cash)
    assert summary["trading_net_pnl"] == "-0.05"
    assert summary["paid_fees"] == "0.02"
    assert summary["max_drawdown"] == "0.00005"


def test_mark_missing_quote_preserves_unavailability() -> None:
    spec = spec_sample()
    history, _ = history_for(spec)
    later = spec.end_at_utc + timedelta(seconds=61)
    extended = spec.model_copy(update={"end_at_utc": later})
    result = mark_point(extended, history, spec.frames, later)
    assert result.nav is None
    assert not result.marks_complete
    assert result.mark_reasons
    assert result.realized_gross == 0


@pytest.mark.parametrize("failure", ["empty", "foreign", "future", "past", "currency"])
def test_mark_rejects_unmatched_or_future_prefix(failure: str) -> None:
    spec = spec_sample()
    history, _ = history_for(spec)
    frames = spec.frames
    at = spec.end_at_utc
    if failure == "empty":
        frames = ()
    elif failure == "foreign":
        frames = (frames[0].model_copy(update={"resolution_terms_hash": "a" * 64}),)
    elif failure == "future":
        at += timedelta(seconds=1)
    elif failure == "past":
        at -= timedelta(seconds=1)
    else:
        history.state = history.state.model_copy(update={"cash_unit": "EUR"})
    with pytest.raises(ValueError, match="metric_point_input_mismatch"):
        mark_point(spec, history, frames, at)


def test_latency_point_is_at_completion_without_future_quote_leakage() -> None:
    spec = spec_sample()
    spec = spec.model_copy(
        update={
            "simulation": spec.simulation.model_copy(update={"latency_ms": 1000}),
            "end_at_utc": spec.end_at_utc + timedelta(seconds=1),
        }
    )
    history, _ = history_for(spec)
    assert mark_point(spec, history, spec.frames, spec.end_at_utc).nav == Decimal("999.95")
    with pytest.raises(ValueError):
        mark_point(spec, history, spec.frames, spec.start_at_utc)


def test_report_drawdown_includes_opening_cash_and_respects_recoveries() -> None:
    points = tuple(point(value, index) for index, value in enumerate(("90", "120", "60", "100")))
    result = summarize(points, (), Decimal(100))
    assert result["max_drawdown"] == "0.5"
    assert result["max_drawdown_amount"] == "60"
    assert result["trading_net_pnl"] == "0"
    assert result["trading_roi"] == "0"
    assert result["nav_coverage"] == {"valid": 4, "total": 4, "complete": True}
    assert result["max_exposure_cost_basis"] == "50"
    assert result["volatility"] is not None
    assert result["volatility_reason"] is None
    with localcontext() as context:
        context.prec = 3
        assert summarize(points, (), Decimal(100)) == result


def test_short_sample_returns_remain_visible_without_volatility_claim() -> None:
    result = summarize((point("100"), point("110", 1)), (), Decimal(100))
    assert result["interval_returns"] == ["0.1"]
    assert result["volatility"] is None


def test_missing_midpoint_invalidates_path_even_if_final_nav_known() -> None:
    result = summarize((point("100"), point(None, 1), point("110", 2)), (), Decimal(100))
    assert result["nav"] == "110"
    assert result["trading_net_pnl"] is None
    assert result["max_drawdown"] is None
    assert result["volatility"] is None
    assert result["drawdown_reason"] == "missing_nav_samples"
    assert result["nav_coverage"] == {"valid": 2, "total": 3, "complete": False}


def test_no_samples_are_unavailable_not_zero() -> None:
    result = summarize((), (), Decimal(100))
    for field in ("nav", "cash", "realized_gross_pnl", "max_drawdown", "trading_net_pnl"):
        assert result[field] is None
    assert result["drawdown_reason"] == "no_samples"
    assert result["counts"] == {
        "decisions": 0,
        "abstentions": 0,
        "proposals": 0,
        "sizing_refusals": 0,
        "orders": 0,
        "fills": 0,
        "partial_fills": 0,
        "rejections": 0,
        "expiries": 0,
        "fill_ratio": None,
    }


def test_regular_sample_volatility_is_decimal_nonannualized_sample_statistic() -> None:
    result = summarize((point("100"), point("110", 1), point("99", 2)), (), Decimal(100))
    with localcontext() as context:
        context.prec = 100
        expected = Decimal("0.02").sqrt()
    assert result["interval_returns"] == ["0.1", "-0.1"]
    assert result["volatility"] == str(expected)
    assert "not_annualized" in str(result["volatility_basis"])


@pytest.mark.parametrize(
    "points,reason",
    [
        ((point("100"),), "at_least_three_nav_samples_required"),
        ((point("100"), point("100", 1)), "at_least_three_nav_samples_required"),
        ((point("100"), point("100", 1), point("100", 3)), "irregular_sample_intervals"),
        ((point("100"), point("0", 1), point("0", 2)), "zero_return_denominator"),
    ],
)
def test_volatility_refuses_insufficient_irregular_or_zero_denominator(
    points: tuple[MetricPoint, ...], reason: str
) -> None:
    result = summarize(points, (), Decimal(100))
    assert result["volatility"] is None
    assert result["volatility_reason"] == reason


@pytest.mark.parametrize("amount", [Decimal(0), Decimal(-1), Decimal("NaN"), 100, 1.2])
def test_invalid_initial_cash(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive_decimal"):
        summarize((), (), amount)


def test_duplicate_instant_points_preserve_drawdown_and_refuse_volatility() -> None:
    result = summarize((point("100"), point("60"), point("110", 1)), (), Decimal(100))
    assert result["max_drawdown"] == "0.4"
    assert result["max_drawdown_amount"] == "40"
    assert result["nav_coverage"] == {"valid": 3, "total": 3, "complete": True}
    assert result["trading_net_pnl"] == "10"
    returns = result["interval_returns"]
    assert isinstance(returns, list) and returns[0] == "-0.4"
    assert result["volatility"] is None
    assert result["volatility_reason"] == "zero_sample_interval"


@pytest.mark.parametrize(
    "points", [(point("100"), point("50")), (point("100"), point("50"), point("100"))]
)
def test_all_points_same_instant_still_have_no_volatility(points: tuple[MetricPoint, ...]) -> None:
    result = summarize(points, (), Decimal(100))
    assert result["max_drawdown"] == "0.5"
    assert result["volatility_reason"] == "zero_sample_interval"


def test_points_must_be_chronological() -> None:
    with pytest.raises(ValueError, match="must_be_chronological"):
        summarize((point("100", 2), point("100")), (), Decimal(100))


def test_currency_mismatch_refused() -> None:
    with pytest.raises(ValueError, match="mixed_metric_currencies"):
        summarize((point("100"), point("100", 1, cash_unit="EUR")), (), Decimal(100))


def test_metric_model_rejects_floats_nonfinite_missing_and_inconsistent_nav() -> None:
    for update in ({"nav": 1.2}, {"nav": "NaN"}, {"marks_complete": False}, {"fees": "-1"}):
        with pytest.raises(ValidationError):
            MetricPoint.model_validate({**point("100").model_dump(), **update})
    with pytest.raises(ValidationError):
        point("100", at_utc=datetime(2026, 10, 20))
    with pytest.raises(ValidationError):
        frozen_field = "cash"
        setattr(point("100"), frozen_field, Decimal(1))


def test_cash_path_and_counts_equal_existing_evaluation_without_ledger_changes() -> None:
    spec = spec_sample()
    evaluation, history = evaluate(spec)
    result = mark_point(spec, history, spec.frames, spec.end_at_utc)
    summary = summarize((result,), evaluation.decisions, spec.initial_cash)
    assert summary["nav"] == "1000"
    assert summary["max_drawdown"] == "0"
    counts = summary["counts"]
    assert isinstance(counts, dict)
    assert counts["abstentions"] == 1 and counts["orders"] == 0


def test_partial_and_rejected_receipts_count_attempts_without_inventing_roundtrips() -> None:
    original = spec_sample()
    history, evaluation = history_for(original)
    assert evaluation.decisions[0].receipt is not None
    receipt = evaluation.decisions[0].receipt
    partial = receipt.model_copy(
        update={"result": receipt.result.model_copy(update={"status": "PARTIALLY_FILLED"})}
    )
    rejected = receipt.model_copy(
        update={
            "result": receipt.result.model_copy(
                update={"status": "REJECTED", "quantity": Decimal(0)}
            )
        }
    )
    records = (
        evaluation.decisions[0].model_copy(update={"receipt": partial}),
        evaluation.decisions[0].model_copy(update={"index": 1, "receipt": rejected}),
        evaluation.decisions[0].model_copy(
            update={"index": 2, "receipt": None, "sizing_reason": "below_step"}
        ),
    )
    result = summarize(
        (mark_point(original, history, original.frames, original.end_at_utc),),
        records,
        original.initial_cash,
    )
    counts = result["counts"]
    assert isinstance(counts, dict)
    assert counts["fills"] == 1 and counts["partial_fills"] == 1
    assert counts["rejections"] == 1 and counts["sizing_refusals"] == 1
    assert counts["fill_ratio"] == "0.5"
    unavailable = result["unavailable"]
    assert isinstance(unavailable, dict)
    assert unavailable["worst_trade"] == "round_trip_trade_matching_not_defined"


def test_prefix_valuation_ignores_later_frame_and_preserves_exact_replay() -> None:
    spec = ResearchInput.model_validate_json(
        (Path(__file__).parents[1] / "fixtures" / "research_probability.json").read_text()
    )
    history = History(spec.run_id, portfolio_config(spec))
    first = spec.frames[0]
    decision = execute_decision(
        spec, history, 0, first, decide(spec.strategy, first, history.state)
    )
    result = mark_point(spec, history, (first,), first.at_utc)
    assert result.realized_gross == 0 and result.open_positions == 1
    assert result.nav == Decimal("99.74")
    assert mark_point(spec, history, (first,), first.at_utc) == result
    assert summarize((result,), (decision,), spec.initial_cash)["trading_net_pnl"] == "-0.26"
