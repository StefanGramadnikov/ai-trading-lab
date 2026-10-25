"""Bounded cells preserve deterministic controls and recorded inference recovery."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid5

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.evidence import build_evidence
from ai_trading_lab.ai.models import AIRequest, Evidence, ModelAnswer, ProviderResult, Role
from ai_trading_lab.ai.orchestration import orchestrate
from ai_trading_lab.ai.providers import FixtureProvider
from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.ai.run_store import projection_hash
from ai_trading_lab.ai.service import InferenceService
from ai_trading_lab.backtesting.models import DecisionRecord, Evaluation, ResearchInput
from ai_trading_lab.backtesting.reporting import report
from ai_trading_lab.backtesting.runner import execute_decision, portfolio_config
from ai_trading_lab.experiments.costs import CostAllocation, Expense
from ai_trading_lab.experiments.metrics import MetricPoint, mark_point, summarize
from ai_trading_lab.experiments.models import Cell, ExperimentConfig, RiskProfile
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import decimal_amount
from ai_trading_lab.strategies.models import Abstain, TradeProposal
from ai_trading_lab.strategies.rules import decide


@dataclass
class CellEvaluation:
    research: ResearchInput
    history: History
    report: dict[str, object]


def suspension(
    profile: RiskProfile, initial: Decimal, peak: Decimal, day_open: Decimal, point: MetricPoint
) -> str | None:
    with localcontext() as ctx:
        ctx.prec = 100
        if point.nav is None:
            return "unavailable_nav"
        if day_open - (point.realized_gross - point.fees) >= initial * profile.daily_loss_fraction:
            return "daily_loss_limit"
        if peak - point.nav >= peak * profile.drawdown_fraction:
            return "drawdown_limit"
    return None


def cell_inference_expenses(reports: tuple[dict[str, object], ...]) -> tuple[Expense, ...]:
    """Estimated/actual inference expenses already frozen into completed cell reports."""
    return tuple(
        Expense.model_validate(item)
        for report_ in reports
        for item in (report_.get("inference_expenses") or [])  # type: ignore[attr-defined]
    )


async def inference_expenses(
    engine: AsyncEngine, cell: Cell, *, snapshot: AsyncConnection | None = None
) -> tuple[Expense, ...]:
    if cell.ai is None:
        return ()
    rows = await BudgetStore(engine).costs_for_run(
        cell.ai.budget.budget_id, cell.research.run_id, snapshot=snapshot
    )
    return tuple(
        Expense(
            expense_id="ai:" + str(row.call_id),
            cost_key="ai:" + str(row.call_id),
            category="ai",
            kind="estimated",
            amount=row.amount,
            currency=row.currency,
            incurred_at_utc=row.at,
            source="budget-call:" + str(row.call_id),
            evidence="synthetic",
            allocations=(CostAllocation(cell_id=cell.cell_id, weight=Decimal(1)),),
            unavailable_reason="unresolved_billing" if row.amount is None else None,
        )
        for row in rows
    )


async def verify_cell_costs(
    engine: AsyncEngine,
    config: ExperimentConfig,
    cell: Cell,
    output: dict[str, object],
    *,
    snapshot: AsyncConnection | None = None,
) -> None:
    expected = [
        e.model_dump(mode="json") for e in await inference_expenses(engine, cell, snapshot=snapshot)
    ]
    if output.get("inference_expenses") != expected:
        raise ValueError("experiment inference cost integrity")


async def evaluate_cell(
    config: ExperimentConfig, cell: Cell, engine: AsyncEngine
) -> CellEvaluation:
    research = cell.profile.effective(cell.research)
    history = History(
        uuid5(config.experiment_id, "cell:" + cell.cell_id), portfolio_config(research)
    )
    budget = BudgetStore(engine)
    provider = FixtureProvider(cell.ai.fixture_results if cell.ai else ())
    service = InferenceService(budget, provider)
    if cell.ai is not None:
        await budget.create(cell.ai.budget)
    records: list[DecisionRecord] = []
    points: list[MetricPoint] = []
    traces: list[dict[str, object]] = []
    halted: str | None = None
    peak = research.initial_cash
    day = research.start_at_utc.date()
    day_open = Decimal(0)
    used_input = used_output = position = 0
    seen: set[str] = set()
    next_at = research.start_at_utc
    latency = timedelta(milliseconds=research.simulation.latency_ms)
    for index, frame in enumerate(research.frames):
        valuation_at = frame.at_utc + latency
        # P&L/fees are booked at execution time, which may cross UTC midnight.
        if valuation_at.date() != day:
            day = valuation_at.date()
            with localcontext() as ctx:
                ctx.prec = 100
                day_open = history.state.realized_gross - history.state.fees
        before = mark_point(research, history, research.frames[: index + 1], valuation_at)
        if before.nav is not None:
            peak = max(peak, before.nav)
        halted = halted or suspension(cell.profile, research.initial_cash, peak, day_open, before)
        baseline = decide(research.strategy, frame, history.state)
        decision: TradeProposal | Abstain = baseline
        flow_trace: object = None
        if isinstance(baseline, TradeProposal) and baseline.side == "buy":
            if halted:
                decision = Abstain(reason=halted)
            elif cell.ai is not None:
                ai = cell.ai
                event_key = content_hash(frame.quote.model_dump(mode="json"))
                eligible = (ai.activation == "event" and event_key not in seen) or (
                    ai.activation == "periodic" and frame.at_utc >= next_at
                )
                if not eligible:
                    decision = Abstain(reason="activation_not_due")
                else:
                    seen.add(event_key)
                    next_at = frame.at_utc + timedelta(seconds=ai.interval_seconds)
                    packet = build_evidence(frame, history.state)
                    step = 0

                    async def invoke(
                        role: Role,
                        prior: tuple[ModelAnswer, ...],
                        ai_config: AIRunInput = ai,
                        frame_index: int = index,
                        evidence: Evidence = packet,
                        virtual_at: datetime = frame.at_utc,
                    ) -> ProviderResult:
                        nonlocal used_input, used_output, position, step
                        max_input = min(
                            ai_config.max_input_tokens,
                            ai_config.flow.total_input_tokens - used_input,
                        )
                        max_output = min(
                            ai_config.max_output_tokens,
                            ai_config.flow.total_output_tokens - used_output,
                        )
                        if max_input <= 0 or max_output <= 0:
                            return ProviderResult(
                                status="error", model=ai_config.model, reason="run_token_limit"
                            )
                        req = AIRequest(
                            call_id=uuid5(
                                config.experiment_id, f"{cell.cell_id}:{frame_index}:{step}"
                            ),
                            provider=ai_config.provider,
                            model=ai_config.model,
                            role=role,
                            evidence=evidence,
                            prior=prior,
                            max_input_tokens=max_input,
                            max_output_tokens=max_output,
                            timeout_seconds=ai_config.request_timeout_seconds,
                        )
                        step += 1
                        provider.bind(req.call_id, position)
                        position += 1
                        result = await service.call(
                            ai_config.budget.budget_id,
                            research.run_id,
                            ai_config.run_spend_limit,
                            req,
                            ai_config.price,
                            virtual_at,
                        )
                        if result.usage is not None:
                            used_input += result.usage.input_tokens
                            used_output += result.usage.output_tokens
                        return result

                    flow = await orchestrate(ai.flow, packet, invoke)
                    flow_trace = flow.model_dump(mode="json")
                    if any(
                        t.result.status in ("unknown", "timeout", "error", "invalid")
                        for t in flow.trace
                    ):
                        halted = "inference_failure"
                    answer = flow.final
                    if answer.action == "buy" and answer.limit_price is not None:
                        decision = TradeProposal(
                            side="buy",
                            limit_price=min(
                                baseline.limit_price, decimal_amount(answer.limit_price)
                            ),
                            reason="ai_validated_candidate",
                            evidence=answer.evidence_ids,
                        )
                    else:
                        decision = Abstain(reason=flow.reason, evidence=answer.evidence_ids)
        records.append(execute_decision(research, history, index, frame, decision))
        after = mark_point(research, history, research.frames[: index + 1], valuation_at)
        if after.nav is not None:
            peak = max(peak, after.nav)
        halted = halted or suspension(cell.profile, research.initial_cash, peak, day_open, after)
        points.append(after)
        traces.append({"index": index, "flow": flow_trace, "entry_suspension": halted})
    # The declared endpoint, not merely the last trade tick, defines the cohort.
    if points[-1].at_utc < research.end_at_utc:
        terminal = mark_point(research, history, research.frames, research.end_at_utc)
        if terminal.nav is not None:
            peak = max(peak, terminal.nav)
        halted = halted or suspension(cell.profile, research.initial_cash, peak, day_open, terminal)
        points.append(terminal)
    evaluation = Evaluation(
        run_id=research.run_id,
        input_hash=research.fingerprint,
        dataset_hash=research.dataset_hash,
        decisions=tuple(records),
        final_state=history.state,
    )
    output = report(research, evaluation, history)
    costs = await inference_expenses(engine, cell)
    answers: list[ProviderResult] = []
    for trace in traces:
        flow_data = trace["flow"]
        if isinstance(flow_data, dict):
            for item in flow_data["trace"]:
                answers.append(ProviderResult.model_validate(item["result"]))
    usage_known = all(a.usage is not None for a in answers)
    elapsed = [a.elapsed_ms for a in answers if a.elapsed_ms is not None]
    ai_metrics = {
        "recorded_calls": len(costs),
        "analysis_results": len(answers),
        "input_tokens": sum(a.usage.input_tokens for a in answers if a.usage)
        if usage_known
        else None,
        "output_tokens": sum(a.usage.output_tokens for a in answers if a.usage)
        if usage_known
        else None,
        "failed_results": sum(a.status != "completed" for a in answers),
        "timeout_results": sum(a.status == "timeout" for a in answers),
        "latency_ms": elapsed,
        "latency_basis": "recorded_provider_monotonic_wall_time_not_simulated_execution_latency",
    }
    output.update(
        cell_id=cell.cell_id,
        profile=cell.profile.model_dump(mode="json"),
        metrics=summarize(tuple(points), tuple(records), research.initial_cash),
        equity_curve=[p.model_dump(mode="json") for p in points],
        ai_traces=traces,
        ai_metrics=ai_metrics,
        inference_expenses=[e.model_dump(mode="json") for e in costs],
        entry_suspended=halted is not None,
        suspension_reason=halted,
        paper_projection_hash=projection_hash(history),
        empirical_performance_claim=False,
    )
    if cell.ai is not None:
        summary = await budget.summary(cell.ai.budget.budget_id)
        output["inference_budget"] = summary
        if summary["unresolved"]:
            output.update(entry_suspended=True, suspension_reason="unresolved_billing")
    return CellEvaluation(research, history, output)
