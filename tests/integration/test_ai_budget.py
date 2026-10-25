"""Real PostgreSQL call reservations; no provider HTTP or credentials."""

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from ai_trading_lab.ai.budget import BudgetStore, Reservation, budgets, calls
from ai_trading_lab.ai.models import ProviderResult, Usage
from tests.ai_budget_helpers import isolated_budgets
from tests.unit.test_ai_budget import AT, completed, policy, pricing, request

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def synthetic_paid_settings(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    monkeypatch.setenv("ATL_AI_TOTAL_LIMIT_USD", "1")
    monkeypatch.setenv("ATL_AI_REVIEWED_PRICES", json.dumps([pricing().model_dump(mode="json")]))
    engine = create_async_engine(database_url)
    try:
        async with isolated_budgets(engine):
            yield
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_budget_concurrent_pending_restart_and_idempotent_finish(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy()
        await store.create(envelope)
        await store.create(envelope)
        run_id = uuid4()
        req = request()

        async def reserve_once() -> Reservation:
            return await store.reserve(envelope.budget_id, req, pricing(), AT, run_id, Decimal("1"))

        results = await asyncio.gather(reserve_once(), reserve_once())
        assert sorted(item.status for item in results) == ["blocked", "new"]
        blocked = await store.reserve(
            envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal("1")
        )
        assert blocked.reason == "unresolved_call"
        result = completed(req)
        assert (
            await BudgetStore(engine).finish(
                envelope.budget_id, req.call_id, req.fingerprint, result
            )
            == result
        )
        assert (
            await store.finish(envelope.budget_id, req.call_id, req.fingerprint, result) == result
        )
        assert (await reserve_once()).result == result
        summary = await store.summary(envelope.budget_id)
        assert summary["spent_estimated"] == "0.0003"
        assert summary["reserved"] == "0" and summary["unresolved"] == 0
        with pytest.raises(ValueError, match="immutable"):
            await store.create(envelope.model_copy(update={"total_limit": Decimal("2")}))
        with pytest.raises(ValueError, match="immutable"):
            await store.finish(
                envelope.budget_id, req.call_id, req.fingerprint, completed(req, reason="changed")
            )
        with pytest.raises(ValueError, match="mismatch"):
            await store.finish(envelope.budget_id, req.call_id, "wrong", result)
        changed = req.model_copy(update={"max_input_tokens": 999})
        with pytest.raises(ValueError, match="immutable"):
            await store.reserve(envelope.budget_id, changed, pricing(), AT, run_id, Decimal("1"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unknown", "timeout", "error", "refused"])
async def test_uncertain_or_missing_usage_holds_full_reservation(
    database_url: str, status: str
) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy()
        await store.create(envelope)
        req = request()
        await store.reserve(envelope.budget_id, req, pricing(), AT, uuid4(), Decimal("1"))
        result = ProviderResult.model_validate(
            dict(status=status, model=req.model, reason="synthetic_uncertain")
        )
        await store.finish(envelope.budget_id, req.call_id, req.fingerprint, result)
        assert (await store.summary(envelope.budget_id))["reserved"] == "0.003"
        next_one = await store.reserve(
            envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal("1")
        )
        assert next_one.reason == "unresolved_call"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_overrun_and_call_caps_survive_restart(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy(max_calls=1)
        await store.create(envelope)
        req = request()
        await store.reserve(envelope.budget_id, req, pricing(), AT, uuid4(), Decimal("1"))
        await store.finish(
            envelope.budget_id,
            req.call_id,
            req.fingerprint,
            completed(req, usage=Usage(input_tokens=2000, output_tokens=2000)),
        )
        assert (await BudgetStore(engine).summary(envelope.budget_id))["reserved"] == "0.006"
        assert (
            await store.reserve(envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal("1"))
        ).status == "blocked"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["policy", "delete_call", "reservation", "result", "calls_hash"]
)
async def test_corruption_fails_closed(database_url: str, corruption: str) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy()
        await store.create(envelope)
        req = request()
        await store.reserve(envelope.budget_id, req, pricing(), AT, uuid4(), Decimal("1"))
        await store.finish(envelope.budget_id, req.call_id, req.fingerprint, completed(req))
        async with engine.begin() as connection:
            if corruption == "delete_call":
                await connection.execute(delete(calls).where(calls.c.call_id == req.call_id))
            elif corruption == "policy":
                await connection.execute(
                    update(budgets)
                    .where(budgets.c.budget_id == envelope.budget_id)
                    .values(policy_hash="0" * 64)
                )
            elif corruption == "calls_hash":
                await connection.execute(
                    update(budgets)
                    .where(budgets.c.budget_id == envelope.budget_id)
                    .values(calls_hash="0" * 64)
                )
            else:
                await connection.execute(
                    update(calls)
                    .where(calls.c.call_id == req.call_id)
                    .values(**{corruption + "_hash": "0" * 64})
                )
        with pytest.raises(ValueError, match="integrity"):
            await store.summary(envelope.budget_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_reservation_leaves_no_call_and_failures_rollback(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy(total_limit="0.002")
        await store.create(envelope)
        reservation = await store.reserve(
            envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal("1")
        )
        assert reservation.reason == "spending_limit"
        assert (await store.summary(envelope.budget_id))["call_count"] == 0
        with pytest.raises(ValueError, match="missing"):
            await store.finish(envelope.budget_id, uuid4(), "missing", completed(request()))
        with pytest.raises(ValueError, match="missing"):
            await store.summary(uuid4())
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(calls.c.call_id).where(calls.c.budget_id == envelope.budget_id)
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_global_concurrent_calls_and_cumulative_cap(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy(total_limit="0.003")
        await store.create(envelope)
        first, second = request(), request()
        one, two = await asyncio.gather(
            store.reserve(envelope.budget_id, first, pricing(), AT, uuid4(), Decimal("1")),
            store.reserve(envelope.budget_id, second, pricing(), AT, uuid4(), Decimal("1")),
        )
        assert sorted((one.status, two.status)) == ["blocked", "new"]
        winner = first if one.status == "new" else second
        await store.finish(
            envelope.budget_id, winner.call_id, winner.fingerprint, completed(winner)
        )
        blocked = await store.reserve(
            envelope.budget_id, request(), pricing(), AT, uuid4(), Decimal("1")
        )
        assert blocked.reason == "spending_limit"
        assert (await store.summary(envelope.budget_id))["call_count"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recorded_call_replay_after_price_expiry(database_url: str) -> None:
    from datetime import timedelta

    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy()
        await store.create(envelope)
        req, run_id = request(), uuid4()
        await store.reserve(envelope.budget_id, req, pricing(), AT, run_id, Decimal("1"))
        result = completed(req)
        await store.finish(envelope.budget_id, req.call_id, req.fingerprint, result)
        duplicate = await store.reserve(
            envelope.budget_id, req, pricing(), AT + timedelta(days=3), run_id, Decimal("1")
        )
        assert duplicate.status == "completed" and duplicate.result == result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reservation_checkpoint_failure_rolls_back(database_url: str) -> None:
    from typing import Any

    from sqlalchemy import event

    engine = create_async_engine(database_url)
    try:
        store = BudgetStore(engine)
        envelope = policy()
        await store.create(envelope)
        req, run_id = request(), uuid4()

        def fail(
            connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
        ) -> None:
            if statement.startswith("UPDATE ai_budgets"):
                raise RuntimeError("injected checkpoint failure")

        event.listen(engine.sync_engine, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="injected"):
                await store.reserve(envelope.budget_id, req, pricing(), AT, run_id, Decimal("1"))
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", fail)
        assert (await store.summary(envelope.budget_id))["call_count"] == 0
        assert (
            await store.reserve(envelope.budget_id, req, pricing(), AT, run_id, Decimal("1"))
        ).status == "new"
    finally:
        await engine.dispose()
