"""Small bounded role graph: no runtime tools or trading authority."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ai_trading_lab.ai.evidence import evidence_failure
from ai_trading_lab.ai.models import (
    Evidence,
    FlowConfig,
    FlowResult,
    ModelAnswer,
    ProviderResult,
    Role,
    Trace,
)

Invoke = Callable[[Role, tuple[ModelAnswer, ...]], Awaitable[ProviderResult]]


async def orchestrate(config: FlowConfig, evidence: Evidence, invoke: Invoke) -> FlowResult:
    config = FlowConfig.model_validate(config.model_dump(mode="json"))
    trace: list[Trace] = []
    prior: list[ModelAnswer] = []
    last_strategy: ModelAnswer | None = None
    input_tokens = output_tokens = 0

    def stop(reason: str) -> FlowResult:
        return FlowResult(
            final=ModelAnswer(
                action="abstain",
                instrument_key=evidence.instrument_key,
                limit_price=None,
                reason=reason,
                evidence_ids=evidence.evidence_ids,
            ),
            trace=tuple(trace),
            reason=reason,
        )

    if (failure := evidence_failure(evidence)) is not None:
        return stop(failure)
    try:
        async with asyncio.timeout(config.timeout_seconds):
            for round_number in range(config.max_rounds):
                for role in config.roles:
                    if len(trace) >= config.max_steps:
                        return stop("step_limit")
                    # The invocation enforces per-call bounds before sending; the
                    # cumulative limits also fail closed after actual reported usage.
                    if (
                        input_tokens >= config.total_input_tokens
                        or output_tokens >= config.total_output_tokens
                    ):
                        return stop("token_limit")
                    result = ProviderResult.model_validate(
                        (await invoke(role, tuple(prior))).model_dump(mode="json")
                    )
                    trace.append(Trace(role=role, round=round_number, result=result))
                    if (
                        result.status != "completed"
                        or result.answer is None
                        or result.usage is None
                    ):
                        return stop("provider_" + result.status)
                    input_tokens += result.usage.input_tokens
                    output_tokens += result.usage.output_tokens
                    if (
                        input_tokens > config.total_input_tokens
                        or output_tokens > config.total_output_tokens
                    ):
                        return stop("token_limit")
                    answer = result.answer
                    if (
                        answer.instrument_key != evidence.instrument_key
                        or not set(answer.evidence_ids).issubset(evidence.evidence_ids)
                        or not answer.evidence_ids
                    ):
                        return stop("unqualified_model_evidence")
                    prior.append(answer)
                    if answer.action in ("veto", "abstain"):
                        return stop("advisory_" + answer.action)
                    if role == "strategy":
                        last_strategy = answer
    except TimeoutError:
        return stop("flow_timeout")
    if last_strategy is None:
        return stop("strategy_missing")
    return FlowResult(final=last_strategy, trace=tuple(trace), reason="completed")
