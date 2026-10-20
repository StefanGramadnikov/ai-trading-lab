"""Durable AI development runs; recorded calls replay without another invoice."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid5

from sqlalchemy import Column, String, Table, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore, calls
from ai_trading_lab.ai.evidence import build_evidence
from ai_trading_lab.ai.models import (
    AIRequest,
    Evidence,
    FlowResult,
    ModelAnswer,
    ProviderResult,
    Role,
)
from ai_trading_lab.ai.orchestration import orchestrate
from ai_trading_lab.ai.providers import FixtureProvider, Provider
from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.ai.service import InferenceService
from ai_trading_lab.backtesting.models import DecisionRecord, Evaluation, runtime_fingerprint
from ai_trading_lab.backtesting.reporting import report
from ai_trading_lab.backtesting.runner import execute_decision, portfolio_config
from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.models import decimal_amount
from ai_trading_lab.paper_execution.store import (
    PaperStore,
    commands,
    events,
    fills,
    instruments,
    journal,
    liquidity,
    orders,
    portfolios,
    projections,
)
from ai_trading_lab.strategies.models import Abstain, TradeProposal
from ai_trading_lab.strategies.rules import decide

ai_runs = Table(
    "ai_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("input_hash", String(64), nullable=False),
    Column("input", JSONB, nullable=False),
    Column("report", JSONB, nullable=False),
    Column("report_hash", String(64), nullable=False),
)


ai_run_starts = Table(
    "ai_run_starts",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("input", JSONB, nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("engine_hash", String(64), nullable=False),
)


def projection_hash(history: History) -> str:
    return content_hash(json.loads(json.dumps(projections(history), default=str)))


class AIRunStore:
    def __init__(
        self, engine: AsyncEngine, provider: Provider, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.engine, self.provider = engine, provider
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(self, spec: AIRunInput) -> dict[str, object]:
        spec = AIRunInput.model_validate(spec.model_dump(mode="json"))
        await self._bind_start(spec)
        budget = BudgetStore(self.engine)
        await budget.create(spec.budget)
        run_id = spec.research.run_id
        # A run lock serializes concurrent identical runs. Budget reservations use
        # separate short transactions and remain durable if this run rolls back.
        async with asyncio.timeout(300), self.engine.begin() as connection:
            lock_key = int(run_id.hex[:16], 16) - (1 << 63)
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            old = (
                (await connection.execute(select(ai_runs).where(ai_runs.c.run_id == run_id)))
                .mappings()
                .one_or_none()
            )
            if old is not None:
                if old["input_hash"] != spec.fingerprint or old["input"] != spec.model_dump(
                    mode="json"
                ):
                    raise ValueError("AI run configuration is immutable")
                saved = cast(dict[str, object], old["report"])
                if (
                    old["report_hash"] != content_hash(saved)
                    or saved.get("engine_hash") != runtime_fingerprint()
                ):
                    raise ValueError("AI run report or runtime integrity failure")
                pid = uuid5(run_id, "ai:" + spec.flow.mode)
                history, config = await PaperStore(self.engine)._load(connection, pid)
                if projection_hash(history) != saved.get(
                    "paper_projection_hash"
                ) or config != portfolio_config(spec.research):
                    raise ValueError("AI run portfolio integrity failure")
                await budget.summary(spec.budget.budget_id)
                return saved
            output, history = await self._evaluate(spec, budget)
            pid = history.state.portfolio_id
            config = portfolio_config(spec.research)
            await connection.execute(
                insert(portfolios).values(
                    portfolio_id=pid,
                    config=config,
                    config_hash=content_hash(config),
                    state=history.state.model_dump(mode="json"),
                    last_event_at=history.last_at,
                )
            )
            projected = projections(history)
            for table in (orders, journal, events, fills, liquidity, instruments, commands):
                if projected[table.name]:
                    await connection.execute(insert(table), projected[table.name])
            await connection.execute(
                insert(ai_runs).values(
                    run_id=run_id,
                    input_hash=spec.fingerprint,
                    input=spec.model_dump(mode="json"),
                    report=output,
                    report_hash=content_hash(output),
                )
            )
            return output

    async def _bind_start(self, spec: AIRunInput) -> None:
        # Commit identity before inference: a failed final report must not allow
        # recorded answers to be applied under changed sizing, policy or code.
        run_id = spec.research.run_id
        payload = spec.model_dump(mode="json")
        engine_hash = runtime_fingerprint()
        async with asyncio.timeout(300), self.engine.begin() as connection:
            lock_key = int(run_id.hex[:16], 16) - (1 << 63)
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            saved = (
                (
                    await connection.execute(
                        select(ai_run_starts).where(ai_run_starts.c.run_id == run_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if saved is None:
                orphan = await connection.scalar(
                    select(calls.c.call_id)
                    .where(calls.c.reservation["run_id"].astext == str(run_id))
                    .limit(1)
                )
                if orphan is not None:
                    raise ValueError("AI run start missing for recorded calls")
                await connection.execute(
                    insert(ai_run_starts).values(
                        run_id=run_id,
                        input=payload,
                        input_hash=spec.fingerprint,
                        engine_hash=engine_hash,
                    )
                )
            elif (
                saved["input"] != payload
                or saved["input_hash"] != spec.fingerprint
                or saved["engine_hash"] != engine_hash
            ):
                raise ValueError("AI run start configuration or runtime is immutable")

    async def _evaluate(
        self, spec: AIRunInput, budget: BudgetStore
    ) -> tuple[dict[str, object], History]:
        tape = spec.research
        history = History(uuid5(tape.run_id, "ai:" + spec.flow.mode), portfolio_config(tape))
        service = InferenceService(budget, self.provider)
        records: list[DecisionRecord] = []
        traces: list[dict[str, object]] = []
        next_at = tape.start_at_utc
        seen: set[str] = set()
        used_input = used_output = 0
        call_position = 0
        for index, frame in enumerate(tape.frames):
            baseline = decide(tape.strategy, frame, history.state)
            decision: TradeProposal | Abstain = baseline
            reason = "deterministic_abstention"
            flow_result: FlowResult | None = None
            # Predefined rule exits remain available even if inference is blocked.
            if isinstance(baseline, TradeProposal) and baseline.side == "sell":
                reason = "deterministic_exit"
            elif isinstance(baseline, TradeProposal):
                event_key = content_hash(frame.quote.model_dump(mode="json"))
                eligible = (spec.activation == "event" and event_key not in seen) or (
                    spec.activation == "periodic" and frame.at_utc >= next_at
                )
                if not eligible:
                    decision = Abstain(reason="activation_not_due")
                    reason = decision.reason
                else:
                    seen.add(event_key)
                    next_at = frame.at_utc + timedelta(seconds=spec.interval_seconds)
                    evidence = build_evidence(frame, history.state)
                    step = 0

                    async def invoke(
                        role: Role,
                        prior: tuple[ModelAnswer, ...],
                        frame_index: int = index,
                        packet: Evidence = evidence,
                        virtual_at: datetime = frame.at_utc,
                    ) -> ProviderResult:
                        nonlocal step, used_input, used_output, call_position
                        max_input = min(
                            spec.max_input_tokens, spec.flow.total_input_tokens - used_input
                        )
                        max_output = min(
                            spec.max_output_tokens, spec.flow.total_output_tokens - used_output
                        )
                        if max_input <= 0 or max_output <= 0:
                            return ProviderResult(
                                status="error", model=spec.model, reason="run_token_limit"
                            )
                        request = AIRequest(
                            call_id=uuid5(tape.run_id, f"ai:{frame_index}:{step}"),
                            provider=spec.provider,
                            model=spec.model,
                            role=role,
                            evidence=packet,
                            prior=prior,
                            max_input_tokens=max_input,
                            max_output_tokens=max_output,
                            timeout_seconds=spec.request_timeout_seconds,
                        )
                        step += 1
                        if isinstance(self.provider, FixtureProvider):
                            self.provider.bind(request.call_id, call_position)
                        call_position += 1
                        result = await service.call(
                            spec.budget.budget_id,
                            tape.run_id,
                            spec.run_spend_limit,
                            request,
                            spec.price,
                            self.clock() if spec.inference_mode == "real" else virtual_at,
                        )
                        if result.usage is not None:
                            used_input += result.usage.input_tokens
                            used_output += result.usage.output_tokens
                        return result

                    flow_result = await orchestrate(spec.flow, evidence, invoke)
                    answer = flow_result.final
                    if answer.action == "buy" and answer.limit_price is not None:
                        price = min(decimal_amount(answer.limit_price), baseline.limit_price)
                        decision = TradeProposal(
                            side="buy",
                            limit_price=price,
                            reason="ai_validated_candidate",
                            evidence=answer.evidence_ids,
                        )
                    else:
                        decision = Abstain(
                            reason=flow_result.reason, evidence=evidence.evidence_ids
                        )
                    reason = flow_result.reason
            records.append(execute_decision(tape, history, index, frame, decision))
            traces.append(
                {
                    "index": index,
                    "activation_reason": reason,
                    "flow": flow_result.model_dump(mode="json") if flow_result else None,
                }
            )
        evaluation = Evaluation(
            run_id=tape.run_id,
            input_hash=tape.fingerprint,
            dataset_hash=tape.dataset_hash,
            decisions=tuple(records),
            final_state=history.state,
        )
        output = report(tape, evaluation, history)
        output.update(
            ai_input_hash=spec.fingerprint,
            architecture=spec.flow.mode,
            inference_mode=spec.inference_mode,
            purpose=spec.purpose,
            ai_traces=traces,
            budget=await budget.summary(spec.budget.budget_id),
            paper_projection_hash=projection_hash(history),
            ai_output_is_calibrated_forecast=False,
            empirical_performance_claim=False,
        )
        return output, history
