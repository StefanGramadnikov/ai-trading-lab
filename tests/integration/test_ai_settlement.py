"""Worst-case settlement on real PostgreSQL: the cap stays true and the envelope stays usable."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.models import ProviderResult
from ai_trading_lab.ai.service import InferenceService
from tests.ai_budget_helpers import isolated_budgets
from tests.unit.test_ai_budget import AT, policy, pricing, request

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def paid_settings(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv("ATL_AI_TOTAL_LIMIT_USD", "0.01")
    monkeypatch.setenv("ATL_AI_REVIEWED_PRICES", json.dumps([pricing().model_dump(mode="json")]))
    engine = create_async_engine(database_url)
    try:
        async with isolated_budgets(engine):
            yield engine
    finally:
        await engine.dispose()


def provider_returning(result: ProviderResult) -> AsyncMock:
    provider = AsyncMock()
    provider.analyze.return_value = result
    # AsyncMock invents any attribute; the service looks these two up and must not find them.
    del provider.preflight, provider.destination
    return provider


async def run(service: InferenceService, envelope_id: object, req: object) -> ProviderResult:
    return await service.call(envelope_id, uuid4(), Decimal("1"), req, pricing(), AT)  # type: ignore[arg-type]


async def test_by_default_an_unknown_outcome_still_freezes_the_envelope(
    paid_settings: AsyncEngine,
) -> None:
    store, envelope = BudgetStore(paid_settings), policy(total_limit="0.01")
    await store.create(envelope)
    lost = ProviderResult(status="timeout", model="test-model", reason="timeout_unknown_billing")
    service = InferenceService(store, provider_returning(lost))
    first = await run(service, envelope.budget_id, request())
    assert first.status == "timeout"
    second = await run(service, envelope.budget_id, request())
    assert (second.status, second.reason) == ("error", "unresolved_call")
    assert (await store.summary(envelope.budget_id))["unresolved"] == 1


@pytest.mark.parametrize("status", ["timeout", "error", "unknown"])
async def test_when_asked_an_unknown_outcome_is_charged_at_its_maximum_and_the_next_call_proceeds(
    paid_settings: AsyncEngine, status: str
) -> None:
    store, envelope = BudgetStore(paid_settings), policy(total_limit="0.01")
    await store.create(envelope)
    lost = ProviderResult(status=status, model="test-model", reason="lost")
    service = InferenceService(store, provider_returning(lost), settle_unknown=True)
    first = await run(service, envelope.budget_id, request())
    assert (first.status, first.reason) == ("settled", "settled_lost")
    summary = await store.summary(envelope.budget_id)
    assert summary["unresolved"] == 0 and summary["spent_estimated"] == "0.003"
    assert await store.settled_since(envelope.budget_id, AT) == 1
    second = await run(service, envelope.budget_id, request())
    assert second.status == "settled"  # reserved and sent again, not refused as unresolved


async def test_settled_charges_count_toward_the_cap_so_money_stays_bounded(
    paid_settings: AsyncEngine,
) -> None:
    store, envelope = BudgetStore(paid_settings), policy(total_limit="0.01")
    await store.create(envelope)
    lost = ProviderResult(status="unknown", model="test-model", reason="lost")
    provider = provider_returning(lost)
    service = InferenceService(store, provider, settle_unknown=True)
    outcomes = [await run(service, envelope.budget_id, request()) for _ in range(5)]
    # 0.003 each against a 0.01 cap: three are sent, the rest are refused before sending.
    assert [o.status for o in outcomes] == ["settled"] * 3 + ["error"] * 2
    assert provider.analyze.await_count == 3
    assert outcomes[-1].reason in ("global_spending_limit", "spending_limit")
    assert Decimal(str((await store.summary(envelope.budget_id))["spent_estimated"])) <= Decimal(
        "0.01"
    )


async def test_settle_pending_charges_only_calls_old_enough_to_be_dead(
    paid_settings: AsyncEngine,
) -> None:
    store, envelope = BudgetStore(paid_settings), policy(total_limit="0.01")
    await store.create(envelope)
    old, young = request(), request()
    assert (
        await store.reserve(envelope.budget_id, old, pricing(), AT, uuid4(), Decimal("1"))
    ).status == "new"
    # A second reservation is refused while the first is unresolved; settle it to continue.
    assert await store.settle_pending(envelope.budget_id, AT) == 0  # not older than the cutoff
    assert await store.settle_pending(envelope.budget_id, AT + timedelta(minutes=10)) == 1
    assert await store.settle_pending(envelope.budget_id, AT + timedelta(minutes=10)) == 0
    summary = await store.summary(envelope.budget_id)
    assert summary["unresolved"] == 0 and summary["spent_estimated"] == "0.003"
    assert (
        await store.reserve(
            envelope.budget_id, young, pricing(), AT + timedelta(minutes=11), uuid4(), Decimal("1")
        )
    ).status == "new"
