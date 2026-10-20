"""Synthetic AI scenario; zero-priced fixtures are never reported as API usage."""

from decimal import Decimal
from uuid import uuid4

from ai_trading_lab.ai.budget import BudgetPolicy, ProviderLimit
from ai_trading_lab.ai.evidence import build_evidence
from ai_trading_lab.ai.models import FlowConfig, ModelAnswer, PriceSnapshot, ProviderResult, Usage
from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.paper_execution.accounting import open_portfolio
from tests.backtest_helpers import research


def ai_spec() -> AIRunInput:
    tape = research()
    state, _ = open_portfolio(uuid4(), "USD", Decimal("0.01"), Decimal(100))
    packet = build_evidence(tape.frames[0], state)
    result = ProviderResult(
        status="completed",
        model="fixture-v1",
        reason="synthetic",
        answer=ModelAnswer(
            action="buy",
            instrument_key=packet.instrument_key,
            limit_price="0.50",
            reason="synthetic-only",
            evidence_ids=(packet.evidence_ids[0],),
        ),
        usage=Usage(input_tokens=20, output_tokens=20),
    )
    return AIRunInput(
        research=tape,
        inference_mode="fixture",
        purpose="development",
        provider="fixture",
        model="fixture-v1",
        price=PriceSnapshot(
            provider="fixture",
            model="fixture-v1",
            input_per_million="0",
            output_per_million="0",
            source_url="fixture://no-billing",
            verified_at_utc=tape.start_at_utc,
            version="synthetic-v1",
        ),
        budget=BudgetPolicy(
            budget_id=uuid4(),
            version="fixture-v1",
            total_limit="0",
            daily_limit="0",
            monthly_limit="0",
            provider_limits=(ProviderLimit(provider="fixture", limit="0"),),
            max_calls=20,
        ),
        run_spend_limit="0",
        flow=FlowConfig(
            mode="hybrid",
            roles=("strategy",),
            max_steps=1,
            max_rounds=1,
            timeout_seconds=2,
            total_input_tokens=32768,
            total_output_tokens=8192,
        ),
        activation="event",
        interval_seconds=1,
        max_input_tokens=16000,
        max_output_tokens=1000,
        request_timeout_seconds=1,
        fixture_results=(result,),
    )
