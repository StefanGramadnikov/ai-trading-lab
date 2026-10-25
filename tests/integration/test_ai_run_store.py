"""Synthetic end-to-end AI decisions use the real isolated PostgreSQL ledger."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4, uuid5

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.models import (
    AIRequest,
    FlowConfig,
    ModelAnswer,
    ProviderName,
    ProviderResult,
)
from ai_trading_lab.ai.providers import FixtureProvider
from ai_trading_lab.ai.run_store import AIRunStore, ai_runs
from ai_trading_lab.config import Settings
from ai_trading_lab.paper_execution.store import PaperStore
from tests.ai_budget_helpers import isolated_budgets
from tests.ai_helpers import ai_spec

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        async with isolated_budgets(engine):
            yield engine
    finally:
        await engine.dispose()


async def test_run_concurrency_and_restart_replays_without_inference(engine: AsyncEngine) -> None:
    spec = ai_spec()

    class Counting(FixtureProvider):
        count = 0

        async def analyze(self, request: AIRequest) -> ProviderResult:
            self.count += 1
            return await super().analyze(request)

    provider = Counting(spec.fixture_results)
    store = AIRunStore(engine, provider, lambda: spec.research.start_at_utc)
    results = await asyncio.gather(store.run(spec), store.run(spec))
    a, b = (cast(dict[str, Any], r) for r in results)
    assert a == b and provider.count == 1
    assert a["counts"]["fills"] == 1
    assert a["inference_mode"] == "fixture" and a["empirical_performance_claim"] is False
    assert await PaperStore(engine).reconcile(uuid5(spec.research.run_id, "ai:hybrid"))
    restarted = AIRunStore(engine, Counting(()), lambda: spec.research.start_at_utc)
    assert await restarted.run(spec) == a
    with pytest.raises(ValueError, match="immutable"):
        await restarted.run(spec.model_copy(update={"interval_seconds": 2}))


@pytest.mark.parametrize("mode", ["sequential", "collaborative"])
async def test_multi_agent_trace_and_bounded_steps(engine: AsyncEngine, mode: str) -> None:
    spec = ai_spec()
    config = FlowConfig(
        mode=mode,
        roles=("vet", "strategy", "risk"),
        max_steps=6,
        max_rounds=2 if mode == "collaborative" else 1,
        timeout_seconds=2,
        total_input_tokens=16000,
        total_output_tokens=1000,
    )
    spec = spec.model_copy(update={"flow": config, "fixture_results": spec.fixture_results * 6})
    out: Any = await AIRunStore(
        engine, FixtureProvider(spec.fixture_results), lambda: spec.research.start_at_utc
    ).run(spec)
    assert len(out["ai_traces"][0]["flow"]["trace"]) == (6 if mode == "collaborative" else 3)
    assert out["counts"]["fills"] == 1


async def test_unknown_outcome_holds_budget_and_abstains(engine: AsyncEngine) -> None:
    spec = ai_spec()
    unknown = ProviderResult(status="unknown", model=spec.model, reason="lost_response")
    out: Any = await AIRunStore(
        engine, FixtureProvider((unknown,)), lambda: spec.research.start_at_utc
    ).run(spec)
    assert out["counts"]["fills"] == 0
    summary = await BudgetStore(engine).summary(spec.budget.budget_id)
    assert summary["unresolved"] == 1
    nextspec = spec.model_copy(
        update={"research": spec.research.model_copy(update={"run_id": uuid4()})}
    )
    out = await AIRunStore(
        engine, FixtureProvider(spec.fixture_results), lambda: spec.research.start_at_utc
    ).run(nextspec)
    assert out["counts"]["fills"] == 0 and out["budget"]["call_count"] == 1


async def test_deterministic_exit_remains_available_after_ai_failure(engine: AsyncEngine) -> None:
    spec = ai_spec()
    first = spec.research.frames[0]
    assert first.forecast is not None
    later = first.model_copy(
        update={
            "at_utc": first.at_utc + timedelta(seconds=1),
            "forecast": first.forecast.model_copy(update={"probability": Decimal("0.1")}),
        }
    )
    spec = spec.model_copy(
        update={"research": spec.research.model_copy(update={"frames": (first, later)})}
    )
    out: Any = await AIRunStore(
        engine, FixtureProvider(spec.fixture_results), lambda: first.at_utc
    ).run(spec)
    assert out["counts"]["fills"] == 2 and not out["open_positions"]
    assert out["ai_traces"][1]["activation_reason"] == "deterministic_exit"
    assert out["budget"]["call_count"] == 1


@pytest.mark.parametrize("damage", ["report", "ledger", "call"])
async def test_integrity_failure_blocks_replay(engine: AsyncEngine, damage: str) -> None:
    from ai_trading_lab.ai.budget import calls
    from ai_trading_lab.paper_execution.store import portfolios

    spec = ai_spec()
    store = AIRunStore(
        engine, FixtureProvider(spec.fixture_results), lambda: spec.research.start_at_utc
    )
    await store.run(spec)
    async with engine.begin() as c:
        if damage == "report":
            await c.execute(
                ai_runs.update().where(ai_runs.c.run_id == spec.research.run_id).values(report={})
            )
        elif damage == "ledger":
            await c.execute(
                portfolios.update()
                .where(portfolios.c.portfolio_id == uuid5(spec.research.run_id, "ai:hybrid"))
                .values(state={})
            )
        else:
            await c.execute(calls.delete().where(calls.c.budget_id == spec.budget.budget_id))
    with pytest.raises(ValueError):
        await store.run(spec)


async def test_advisory_veto_never_reaches_paper_order(engine: AsyncEngine) -> None:
    spec = ai_spec()
    original = spec.fixture_results[0]
    assert original.answer is not None
    veto = original.model_copy(
        update={
            "answer": ModelAnswer(
                action="veto",
                instrument_key=original.answer.instrument_key,
                limit_price=None,
                reason="risk_reject",
                evidence_ids=original.answer.evidence_ids,
            )
        }
    )
    out: Any = await AIRunStore(
        engine, FixtureProvider((veto,)), lambda: spec.research.start_at_utc
    ).run(spec)
    assert out["counts"]["orders"] == 0 and out["counts"]["abstentions"] == 1


async def test_provider_model_mismatch_keeps_budget_unresolved(engine: AsyncEngine) -> None:
    import httpx2
    from pydantic import SecretStr

    from ai_trading_lab.ai.providers import HTTPProvider
    from ai_trading_lab.ai.service import InferenceService
    from tests.contract.test_ai_providers import request, response
    from tests.unit.test_ai_budget import AT, policy, pricing

    req = request()
    price = pricing().model_copy(update={"provider": "openai", "model": req.model})
    budget = BudgetStore(
        engine, Settings(_env_file=None, ai_total_limit_usd="1", ai_reviewed_prices=(price,))
    )
    envelope = policy()
    await budget.create(envelope)
    payload = response(model="unexpected-expensive-model")
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    ) as client:
        service = InferenceService(budget, HTTPProvider("openai", SecretStr("synthetic"), client))
        result = await service.call(envelope.budget_id, uuid4(), Decimal(1), req, price, AT)
    assert result.status == "invalid" and result.usage is None
    assert result.elapsed_ms is not None and result.completed_at_utc is not None
    assert (await budget.summary(envelope.budget_id))["unresolved"] == 1


async def test_lost_run_transaction_replays_calls_without_second_provider_request(
    engine: AsyncEngine,
) -> None:
    from typing import Any

    from sqlalchemy import event

    spec = ai_spec()
    provider = FixtureProvider(spec.fixture_results)

    def fail(c: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if statement.startswith("INSERT INTO ai_runs"):
            raise RuntimeError("injected final transaction failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            await AIRunStore(engine, provider).run(spec)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail)
    out = await AIRunStore(engine, FixtureProvider(())).run(spec)
    assert isinstance(out["counts"], dict) and out["counts"]["fills"] == 1
    assert (await BudgetStore(engine).summary(spec.budget.budget_id))["call_count"] == 1


@pytest.mark.parametrize("activation", ["periodic", "event"])
async def test_activation_dedup_and_deterministic_risk(
    engine: AsyncEngine, activation: str
) -> None:
    spec = ai_spec()
    frame = spec.research.frames[0]
    # Declining the first candidate leaves no position, so repeated candidate
    # delivery exercises trigger deduplication rather than a held-position filter.
    first = spec.fixture_results[0]
    assert first.answer is not None
    declined = first.model_copy(
        update={
            "answer": first.answer.model_copy(update={"action": "abstain", "limit_price": None})
        }
    )
    spec = spec.model_copy(
        update={
            "activation": activation,
            "research": spec.research.model_copy(update={"frames": (frame, frame)}),
        }
    )
    out: Any = await AIRunStore(engine, FixtureProvider((declined,))).run(spec)
    assert out["budget"]["call_count"] == 1
    assert out["ai_traces"][1]["activation_reason"] == "activation_not_due"
    assert out["counts"]["orders"] == 0


async def test_fixture_clock_is_independent_of_host_time(engine: AsyncEngine) -> None:
    spec = ai_spec()
    far_future = spec.research.start_at_utc + timedelta(days=1000)
    out = await AIRunStore(engine, FixtureProvider(spec.fixture_results), lambda: far_future).run(
        spec
    )
    assert isinstance(out["counts"], dict) and out["counts"]["fills"] == 1


async def test_partial_recovery_keeps_fixture_role_positions(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = ai_spec()
    first = spec.fixture_results[0]
    assert first.answer is not None
    veto = first.model_copy(
        update={"answer": first.answer.model_copy(update={"action": "veto", "limit_price": None})}
    )
    spec = spec.model_copy(
        update={
            "flow": FlowConfig(
                mode="sequential",
                roles=("vet", "strategy"),
                max_steps=2,
                max_rounds=1,
                timeout_seconds=10,
                total_input_tokens=32768,
                total_output_tokens=8192,
            ),
            "fixture_results": (first, veto),
        }
    )
    original = BudgetStore.reserve
    attempts = 0

    async def interrupted(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("interrupted between calls")
        return await original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(BudgetStore, "reserve", interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            await AIRunStore(engine, FixtureProvider(spec.fixture_results)).run(spec)
    assert (await BudgetStore(engine).summary(spec.budget.budget_id))["call_count"] == 1
    out: Any = await AIRunStore(engine, FixtureProvider(spec.fixture_results)).run(spec)
    assert out["counts"]["fills"] == 0
    trace = out["ai_traces"][0]["flow"]["trace"]
    assert [item["result"]["answer"]["action"] for item in trace] == ["buy", "veto"]
    assert (await BudgetStore(engine).summary(spec.budget.budget_id))["call_count"] == 2


@pytest.mark.parametrize("feature", ["openai_tool", "anthropic_tool", "anthropic_usage"])
async def test_unsupported_billing_features_hold_and_block(
    engine: AsyncEngine, feature: str
) -> None:
    import httpx2
    from pydantic import SecretStr

    from ai_trading_lab.ai.providers import HTTPProvider
    from ai_trading_lab.ai.service import InferenceService
    from tests.contract.test_ai_providers import request, response
    from tests.unit.test_ai_budget import AT, policy, pricing

    provider: ProviderName = "openai" if feature == "openai_tool" else "anthropic"
    req = request(provider=provider)
    payload = response(provider=provider)
    if feature == "openai_tool":
        payload["output"] = [{"type": "web_search_call", "id": "tool"}]
    elif feature == "anthropic_tool":
        payload["content"] = [{"type": "server_tool_use", "id": "tool"}]
    else:
        payload["usage"] = {
            "input_tokens": 10,
            "output_tokens": 5,
            "server_tool_use": {"web_search_requests": 1},
        }
    price = pricing().model_copy(update={"provider": provider, "model": req.model})
    budget = BudgetStore(
        engine, Settings(_env_file=None, ai_total_limit_usd="1", ai_reviewed_prices=(price,))
    )
    envelope = policy().model_copy(update={"provider_limits": ()})
    await budget.create(envelope)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    ) as client:
        result = await InferenceService(
            budget, HTTPProvider(provider, SecretStr("fake"), client)
        ).call(envelope.budget_id, uuid4(), Decimal(1), req, price, AT)
    assert result.status == "invalid" and result.usage is None
    assert (await budget.summary(envelope.budget_id))["unresolved"] == 1
    next_request = req.model_copy(update={"call_id": uuid4()})
    blocked = await budget.reserve(envelope.budget_id, next_request, price, AT, uuid4(), Decimal(1))
    assert blocked.status == "blocked" and blocked.reason == "unresolved_call"


@pytest.mark.parametrize("change", ["sizing", "risk", "runtime"])
async def test_interrupted_run_keeps_full_configuration_and_runtime_bound(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from sqlalchemy import event

    import ai_trading_lab.ai.run_store as runtime

    spec = ai_spec()

    def fail(c: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if statement.startswith("INSERT INTO ai_runs"):
            raise RuntimeError("injected final transaction failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await AIRunStore(engine, FixtureProvider(spec.fixture_results)).run(spec)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail)
    changed = spec
    if change == "sizing":
        changed = spec.model_copy(
            update={"research": spec.research.model_copy(update={"order_notional": Decimal(20)})}
        )
    elif change == "risk":
        changed = spec.model_copy(
            update={
                "research": spec.research.model_copy(
                    update={
                        "simulation": spec.research.simulation.model_copy(
                            update={"max_order_notional": Decimal(50)}
                        )
                    }
                )
            }
        )
    with monkeypatch.context() as patch:
        if change == "runtime":
            patch.setattr(runtime, "runtime_fingerprint", lambda: "f" * 64)
        with pytest.raises(ValueError, match="start configuration or runtime is immutable"):
            await AIRunStore(engine, FixtureProvider(())).run(changed)
    out: Any = await AIRunStore(engine, FixtureProvider(())).run(spec)
    assert out["counts"]["fills"] == 1
    assert (await BudgetStore(engine).summary(spec.budget.budget_id))["call_count"] == 1


async def test_missing_start_cannot_rebind_recorded_calls(engine: AsyncEngine) -> None:
    from sqlalchemy import delete

    from ai_trading_lab.ai.run_store import ai_run_starts

    spec = ai_spec()
    await AIRunStore(engine, FixtureProvider(spec.fixture_results)).run(spec)
    async with engine.begin() as connection:
        await connection.execute(
            delete(ai_run_starts).where(ai_run_starts.c.run_id == spec.research.run_id)
        )
    with pytest.raises(ValueError, match="start missing"):
        await AIRunStore(engine, FixtureProvider(())).run(spec)
    assert (await BudgetStore(engine).summary(spec.budget.budget_id))["call_count"] == 1


async def test_run_start_migration_preserves_completed_identity(
    engine: AsyncEngine, database_url: str
) -> None:
    import os
    import subprocess

    from sqlalchemy import select

    from ai_trading_lab.ai.run_store import ai_run_starts

    spec = ai_spec()
    out = await AIRunStore(engine, FixtureProvider(spec.fixture_results)).run(spec)
    env = {**os.environ, "ATL_DATABASE_URL": database_url, "ATL_EXECUTION_MODE": "PAPER_ONLY"}
    await asyncio.to_thread(
        subprocess.run,
        ["alembic", "downgrade", "0005_ai_runtime"],
        env=env,
        check=True,
        capture_output=True,
    )
    try:
        await asyncio.to_thread(
            subprocess.run, ["alembic", "upgrade", "head"], env=env, check=True, capture_output=True
        )
    finally:
        # Ensure subsequent tests see the head even if the first migration fails.
        await asyncio.to_thread(
            subprocess.run, ["alembic", "upgrade", "head"], env=env, check=True, capture_output=True
        )
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(ai_run_starts).where(ai_run_starts.c.run_id == spec.research.run_id)
                )
            )
            .mappings()
            .one()
        )
    assert row["input"] == spec.model_dump(mode="json")
    assert row["input_hash"] == spec.fingerprint and row["engine_hash"] == out["engine_hash"]
    assert await AIRunStore(engine, FixtureProvider(())).run(spec) == out
