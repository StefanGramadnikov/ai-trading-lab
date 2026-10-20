"""Pure paper metrics with explicit valuation coverage and no annualization."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.backtesting.models import DecisionRecord, Evaluation, ResearchInput
from ai_trading_lab.backtesting.reporting import report
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.paper_execution.history import History, clock
from ai_trading_lab.paper_execution.models import Amount, NonnegativeAmount
from ai_trading_lab.strategies.models import StrategyFrame


class MetricPoint(FrozenModel):
    at_utc: AwareDatetime
    cash_unit: str = Field(min_length=1, max_length=200)
    nav: NonnegativeAmount | None
    cash: NonnegativeAmount
    realized_gross: Amount
    fees: NonnegativeAmount
    exposure_cost_basis: NonnegativeAmount
    open_positions: int = Field(ge=0, strict=True)
    marks_complete: bool = Field(strict=True)
    mark_reasons: tuple[str, ...] = ()

    @field_validator("at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coverage(self) -> "MetricPoint":
        if self.marks_complete != (self.nav is not None):
            raise ValueError("metric_mark_coverage_mismatch")
        return self


def mark_point(
    spec: ResearchInput, history: History, frames: tuple[StrategyFrame, ...], at: datetime
) -> MetricPoint:
    """Mark only an exact tape prefix using M4's consumed-depth liquidation model."""
    at = clock(at)
    spec = ResearchInput.model_validate(spec.model_dump(mode="json"))
    if (
        not frames
        or frames != spec.frames[: len(frames)]
        or not spec.start_at_utc <= at <= spec.end_at_utc
        or (history.last_at is not None and at < history.last_at)
        or history.state.initial_cash != spec.initial_cash
        or history.state.cash_unit != spec.cash_unit
        or history.state.cash_step != spec.cash_step
    ):
        raise ValueError("metric_point_input_mismatch")
    prefix = ResearchInput.model_validate({**spec.model_dump(), "frames": frames, "end_at_utc": at})
    evaluation = Evaluation(
        run_id=prefix.run_id,
        input_hash=prefix.fingerprint,
        dataset_hash=prefix.dataset_hash,
        decisions=(),
        final_state=history.state,
    )
    output = report(prefix, evaluation, history)
    nav = output["nav"]
    positions = output["open_positions"]
    assert isinstance(nav, str) or nav is None
    assert isinstance(positions, list)
    reasons = tuple(str(position["mark_reason"]) for position in positions)
    with localcontext() as context:
        context.prec = 100
        return MetricPoint(
            at_utc=at,
            cash_unit=history.state.cash_unit,
            nav=Decimal(nav) if nav is not None else None,
            cash=history.state.cash,
            realized_gross=history.state.realized_gross,
            fees=history.state.fees,
            exposure_cost_basis=sum(
                (position.cost_basis for position in history.state.positions), Decimal(0)
            ),
            open_positions=len(history.state.positions),
            marks_complete=nav is not None,
            mark_reasons=reasons,
        )


def summarize(
    points: tuple[MetricPoint, ...],
    decisions: tuple[DecisionRecord, ...],
    initial_cash: Decimal,
) -> dict[str, object]:
    """Summarize observed marks. Gaps invalidate path metrics rather than hiding losses."""
    # Validate public inputs again: trusted-code model_copy must not bypass the contract.
    points = tuple(MetricPoint.model_validate(point.model_dump()) for point in points)
    decisions = tuple(DecisionRecord.model_validate(record.model_dump()) for record in decisions)
    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise ValueError("positive_decimal_initial_cash_required")
    if any(right.at_utc < left.at_utc for left, right in zip(points, points[1:], strict=False)):
        raise ValueError("metric_points_must_be_chronological")
    if len({point.cash_unit for point in points}) > 1:
        raise ValueError("mixed_metric_currencies")
    with localcontext() as context:
        context.prec = 100
        return _summarize(points, decisions, initial_cash)


def _summarize(
    points: tuple[MetricPoint, ...], decisions: tuple[DecisionRecord, ...], initial_cash: Decimal
) -> dict[str, object]:
    final = points[-1] if points else None
    valid = sum(point.nav is not None for point in points)
    complete = bool(points) and valid == len(points)
    path_reason = None if complete else "missing_nav_samples" if points else "no_samples"
    peak = initial_cash
    drawdown = Decimal(0)
    drawdown_amount = Decimal(0)
    if complete:
        for point in points:
            assert point.nav is not None
            peak = max(peak, point.nav)
            loss = peak - point.nav
            drawdown_amount = max(drawdown_amount, loss)
            drawdown = max(drawdown, loss / peak)
    returns: list[Decimal] = []
    volatility: Decimal | None = None
    volatility_reason = path_reason
    intervals = tuple(
        right.at_utc - left.at_utc for left, right in zip(points, points[1:], strict=False)
    )
    if complete:
        if not any(point.nav == 0 for point in points[:-1]):
            for left, right in zip(points, points[1:], strict=False):
                assert left.nav is not None and right.nav is not None
                returns.append((right.nav - left.nav) / left.nav)
        if any(interval == timedelta(0) for interval in intervals):
            volatility_reason = "zero_sample_interval"
        elif len(points) < 3:
            volatility_reason = "at_least_three_nav_samples_required"
        elif len(set(intervals)) != 1:
            volatility_reason = "irregular_sample_intervals"
        elif any(point.nav == 0 for point in points[:-1]):
            volatility_reason = "zero_return_denominator"
        else:
            mean = sum(returns, Decimal(0)) / len(returns)
            variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (
                len(returns) - 1
            )
            volatility = variance.sqrt()
    receipts = [record.receipt for record in decisions if record.receipt is not None]
    trading_net = final.nav - initial_cash if complete and final and final.nav is not None else None
    return {
        "cash_unit": final.cash_unit if final else None,
        "initial_cash": str(initial_cash),
        "nav": str(final.nav) if final and final.nav is not None else None,
        "cash": str(final.cash) if final else None,
        "realized_gross_pnl": str(final.realized_gross) if final else None,
        "paid_fees": str(final.fees) if final else None,
        "trading_net_pnl": str(trading_net) if trading_net is not None else None,
        "trading_roi": str(trading_net / initial_cash) if trading_net is not None else None,
        "trading_net_reason": path_reason,
        "max_drawdown": str(drawdown) if complete else None,
        "max_drawdown_amount": str(drawdown_amount) if complete else None,
        "drawdown_reason": path_reason,
        "max_exposure_cost_basis": str(max(point.exposure_cost_basis for point in points))
        if points
        else None,
        "final_open_positions": final.open_positions if final else None,
        "nav_coverage": {"valid": valid, "total": len(points), "complete": complete},
        "interval_returns": [str(value) for value in returns],
        "volatility": str(volatility) if volatility is not None else None,
        "volatility_reason": volatility_reason,
        "volatility_basis": "sample_standard_deviation_of_regular_simple_returns_not_annualized",
        "counts": {
            "decisions": len(decisions),
            "abstentions": sum(record.decision.kind == "abstain" for record in decisions),
            "proposals": sum(record.decision.kind == "proposal" for record in decisions),
            "sizing_refusals": sum(record.sizing_reason is not None for record in decisions),
            "orders": len(receipts),
            "fills": sum(receipt.result.quantity > 0 for receipt in receipts),
            "partial_fills": sum(
                receipt.result.status == "PARTIALLY_FILLED" for receipt in receipts
            ),
            "rejections": sum(receipt.result.status == "REJECTED" for receipt in receipts),
            "expiries": sum(receipt.result.status == "EXPIRED" for receipt in receipts),
            "fill_ratio": str(
                Decimal(sum(receipt.result.quantity > 0 for receipt in receipts)) / len(receipts)
            )
            if receipts
            else None,
        },
        "unavailable": {
            "worst_trade": "round_trip_trade_matching_not_defined",
            "holding_period": "round_trip_trade_matching_not_defined",
            "calibration": "resolved_outcomes_and_calibrated_forecasts_required",
            "tail_outcomes": "prespecified_tail_method_and_sufficient_samples_required",
            "underwater_time": "continuous_valuation_coverage_not_established",
        },
        "mark_basis": "modeled_full_liquidation_after_execution_costs",
    }
