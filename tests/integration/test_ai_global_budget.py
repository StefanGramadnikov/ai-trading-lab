"""Synthetic paid identities test database-wide guards without provider traffic."""

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.models import ProviderResult
from tests.ai_budget_helpers import isolated_budgets
from tests.unit.test_ai_budget import AT, completed, policy, pricing, request

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def paid_engine(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv("ATL_AI_TOTAL_LIMIT_USD", "0.003")
    monkeypatch.setenv("ATL_AI_REVIEWED_PRICES", json.dumps([pricing().model_dump(mode="json")]))
    engine = create_async_engine(database_url)
    try:
        async with isolated_budgets(engine):
            yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["pending", "timeout"])
async def test_new_envelope_cannot_bypass_unresolved_provider(
    paid_engine: AsyncEngine, outcome: str
) -> None:
    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    req = request()
    assert (
        await store.reserve(first.budget_id, req, pricing(), AT, uuid4(), Decimal(1))
    ).status == "new"
    if outcome == "timeout":
        await store.finish(
            first.budget_id,
            req.call_id,
            req.fingerprint,
            ProviderResult(status="timeout", model=req.model, reason="synthetic"),
        )
    restarted = BudgetStore(paid_engine)
    result = await restarted.reserve(
        second.budget_id, request(), pricing(), AT, uuid4(), Decimal(1)
    )
    assert result.reason == "provider_unresolved_call"
    assert (await restarted.summary(second.budget_id))["call_count"] == 0


@pytest.mark.asyncio
async def test_new_envelope_cannot_reset_aggregate_cap(paid_engine: AsyncEngine) -> None:
    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    req = request()
    await store.reserve(first.budget_id, req, pricing(), AT, uuid4(), Decimal(1))
    await store.finish(first.budget_id, req.call_id, req.fingerprint, completed(req))
    result = await BudgetStore(paid_engine).reserve(
        second.budget_id, request(), pricing(), AT, uuid4(), Decimal(1)
    )
    assert result.reason == "global_spending_limit"


@pytest.mark.asyncio
async def test_run_file_cannot_understate_reviewed_tariff(paid_engine: AsyncEngine) -> None:
    store = BudgetStore(paid_engine)
    envelope = policy()
    await store.create(envelope)
    cheaper = pricing().model_copy(update={"input_per_million": Decimal("0.1")})
    result = await store.reserve(envelope.budget_id, request(), cheaper, AT, uuid4(), Decimal(1))
    assert result.reason == "pricing_not_reviewed"


@pytest.mark.asyncio
async def test_distinct_envelopes_concurrently_reserve_once(paid_engine: AsyncEngine) -> None:
    store = BudgetStore(paid_engine)
    envelopes = [policy(), policy()]
    for envelope in envelopes:
        await store.create(envelope)
    results = await asyncio.gather(
        *(
            store.reserve(envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal(1))
            for envelope in envelopes
        )
    )
    assert sorted(result.status for result in results) == ["blocked", "new"]


@pytest.mark.asyncio
async def test_other_provider_hold_counts_against_shared_cap(
    paid_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_price = pricing().model_copy(update={"provider": "anthropic"})
    monkeypatch.setenv(
        "ATL_AI_REVIEWED_PRICES",
        json.dumps([p.model_dump(mode="json") for p in (pricing(), other_price)]),
    )
    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    results = await asyncio.gather(
        store.reserve(first.budget_id, request(), pricing(), AT, uuid4(), Decimal(1)),
        store.reserve(
            second.budget_id,
            request().model_copy(update={"provider": "anthropic"}),
            other_price,
            AT,
            uuid4(),
            Decimal(1),
        ),
    )
    assert sorted(result.status for result in results) == ["blocked", "new"]
    assert (
        next(result.reason for result in results if result.status == "blocked")
        == "global_spending_limit"
    )


@pytest.mark.asyncio
async def test_recovered_pending_result_releases_provider_guard_and_replay_ignores_disabled_config(
    paid_engine: AsyncEngine,
) -> None:
    from ai_trading_lab.config import Settings

    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    req, run_id = request(), uuid4()
    await store.reserve(first.budget_id, req, pricing(), AT, run_id, Decimal(1))
    await store.finish(first.budget_id, req.call_id, req.fingerprint, completed(req))
    disabled = BudgetStore(
        paid_engine, Settings(_env_file=None, ai_total_limit_usd="0", ai_reviewed_prices=())
    )
    assert (
        await disabled.reserve(first.budget_id, req, pricing(), AT, run_id, Decimal(1))
    ).status == "completed"
    small = request().model_copy(update={"max_input_tokens": 100, "max_output_tokens": 100})
    assert (
        await store.reserve(second.budget_id, small, pricing(), AT, uuid4(), Decimal(1))
    ).status == "new"


@pytest.mark.asyncio
async def test_paid_defaults_disabled_but_fixture_remains_available(
    paid_engine: AsyncEngine,
) -> None:
    from ai_trading_lab.config import Settings

    store = BudgetStore(
        paid_engine, Settings(_env_file=None, ai_total_limit_usd="0", ai_reviewed_prices=())
    )
    envelope = policy()
    await store.create(envelope)
    assert (
        await store.reserve(envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal(1))
    ).reason == "paid_spending_disabled"
    fixture = request().model_copy(update={"provider": "fixture"})
    price = pricing().model_copy(
        update={
            "provider": "fixture",
            "input_per_million": Decimal(0),
            "output_per_million": Decimal(0),
        }
    )
    assert (
        await store.reserve(envelope.budget_id, fixture, price, AT, uuid4(), Decimal(1))
    ).status == "new"


@pytest.mark.asyncio
async def test_unresolved_provider_does_not_block_other_provider_with_budget(
    paid_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_price = pricing().model_copy(update={"provider": "anthropic"})
    monkeypatch.setenv("ATL_AI_TOTAL_LIMIT_USD", "0.006")
    monkeypatch.setenv(
        "ATL_AI_REVIEWED_PRICES",
        json.dumps([p.model_dump(mode="json") for p in (pricing(), other_price)]),
    )
    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    await store.reserve(first.budget_id, request(), pricing(), AT, uuid4(), Decimal(1))
    result = await store.reserve(
        second.budget_id,
        request().model_copy(update={"provider": "anthropic"}),
        other_price,
        AT,
        uuid4(),
        Decimal(1),
    )
    assert result.status == "new"  # Exact aggregate boundary includes both holds.


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["checkpoint", "delete_last_call"])
async def test_corrupt_paid_history_in_other_envelope_fails_closed(
    paid_engine: AsyncEngine,
    corruption: str,
) -> None:
    from sqlalchemy import delete, update

    from ai_trading_lab.ai.budget import budgets, calls

    store = BudgetStore(paid_engine)
    first, second = policy(), policy()
    await store.create(first)
    await store.create(second)
    req = request()
    await store.reserve(first.budget_id, req, pricing(), AT, uuid4(), Decimal(1))
    await store.finish(first.budget_id, req.call_id, req.fingerprint, completed(req))
    async with paid_engine.begin() as connection:
        if corruption == "checkpoint":
            await connection.execute(
                update(budgets)
                .where(budgets.c.budget_id == first.budget_id)
                .values(calls_hash="0" * 64)
            )
        else:
            await connection.execute(delete(calls).where(calls.c.budget_id == first.budget_id))
    with pytest.raises(ValueError, match="call_history_integrity"):
        await store.reserve(second.budget_id, request(), pricing(), AT, uuid4(), Decimal(1))
    assert (await store.summary(second.budget_id))["call_count"] == 0
