"""Durable conservative AI spending reservations; never send HTTP inside a transaction."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID as PyUUID

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import Column, DateTime, ForeignKey, String, Table, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.ai.models import AIRequest, PriceSnapshot, ProviderName, ProviderResult
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import content_hash, metadata
from ai_trading_lab.paper_execution.models import NonnegativeAmount


class ProviderLimit(FrozenModel):
    provider: ProviderName
    limit: NonnegativeAmount


class BudgetPolicy(FrozenModel):
    budget_id: PyUUID
    version: str = Field(min_length=1, max_length=200)
    currency: Literal["USD"] = "USD"
    total_limit: NonnegativeAmount
    daily_limit: NonnegativeAmount
    monthly_limit: NonnegativeAmount
    provider_limits: tuple[ProviderLimit, ...] = Field(default=(), max_length=4)
    max_calls: int = Field(ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def unique(self) -> BudgetPolicy:
        if len({item.provider for item in self.provider_limits}) != len(self.provider_limits):
            raise ValueError("duplicate_provider_limit")
        return self


class Reservation(FrozenModel):
    status: Literal["new", "completed", "blocked"]
    reason: str
    reserved_amount: NonnegativeAmount
    result: ProviderResult | None = None


class RunCost(FrozenModel):
    call_id: PyUUID
    provider: ProviderName
    at: AwareDatetime
    amount: NonnegativeAmount | None
    held: NonnegativeAmount
    currency: Literal["USD"] = "USD"


class _Call(FrozenModel):
    call_id: PyUUID
    request: AIRequest
    price: PriceSnapshot
    run_id: PyUUID
    run_limit: NonnegativeAmount
    at: AwareDatetime
    reservation: NonnegativeAmount

    @field_validator("at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid(self) -> _Call:
        if self.call_id != self.request.call_id or self.reservation != reserve_cost(
            self.request, self.price, self.at
        ):
            raise ValueError("call_reservation_integrity")
        return self


budgets = Table(
    "ai_budgets",
    metadata,
    Column("budget_id", UUID(as_uuid=True), primary_key=True),
    Column("policy", JSONB, nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("calls_hash", String(64), nullable=False),
)
calls = Table(
    "ai_calls",
    metadata,
    Column("call_id", UUID(as_uuid=True), primary_key=True),
    Column("budget_id", UUID(as_uuid=True), ForeignKey("ai_budgets.budget_id"), nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("reservation", JSONB, nullable=False),
    Column("reservation_hash", String(64), nullable=False),
    Column("result", JSONB, nullable=True),
    Column("result_hash", String(64), nullable=True),
)


def reserve_cost(request: AIRequest, price: PriceSnapshot, at: datetime) -> Decimal:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("aware_budget_clock_required")
    if price.provider != request.provider or price.model != request.model:
        raise ValueError("pricing_identity_mismatch")
    if not timedelta(0) <= at - price.verified_at_utc <= timedelta(days=1):
        raise ValueError("pricing_not_current")
    if request.provider != "fixture" and price.input_per_million + price.output_per_million <= 0:
        raise ValueError("paid_provider_requires_pricing")
    return _cost(price, request.max_input_tokens, request.max_output_tokens)


def _cost(price: PriceSnapshot, input_tokens: int, output_tokens: int) -> Decimal:
    # No cached/batch discount is assumed; provider invoice rounding is not inferred.
    with localcontext() as context:
        context.prec = 100
        return (
            input_tokens * price.input_per_million + output_tokens * price.output_per_million
        ) / Decimal(1_000_000)


def _total(values: Iterable[Decimal], start: Decimal = Decimal(0)) -> Decimal:
    with localcontext() as context:
        context.prec = 100
        return sum(values, start)


def _outcome(call: _Call, result: ProviderResult | None) -> tuple[Decimal, bool]:
    if result is None or result.usage is None:
        return call.reservation, True
    amount = _cost(call.price, result.usage.input_tokens, result.usage.output_tokens)
    exceeded = (
        result.usage.input_tokens > call.request.max_input_tokens
        or result.usage.output_tokens > call.request.max_output_tokens
        or amount > call.reservation
        or result.model != call.request.model
        or result.status not in ("completed", "refused", "invalid")
    )
    return (max(call.reservation, amount), True) if exceeded else (amount, False)


class BudgetStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(self, policy: BudgetPolicy) -> None:
        payload = policy.model_dump(mode="json")
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(budgets)
                .values(
                    budget_id=policy.budget_id,
                    policy=payload,
                    policy_hash=content_hash(payload),
                    calls_hash=content_hash([]),
                )
                .on_conflict_do_nothing()
            )
            saved, _ = await self._load(connection, policy.budget_id)
            if saved != policy:
                raise ValueError("budget_policy_immutable")

    async def _load(
        self, connection: AsyncConnection, budget_id: PyUUID
    ) -> tuple[BudgetPolicy, list[tuple[_Call, ProviderResult | None]]]:
        row = (
            (
                await connection.execute(
                    select(budgets).where(budgets.c.budget_id == budget_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("budget_missing")
        policy = BudgetPolicy.model_validate(row["policy"])
        if policy.budget_id != budget_id or row["policy_hash"] != content_hash(row["policy"]):
            raise ValueError("budget_integrity")
        rows = (
            (
                await connection.execute(
                    select(calls)
                    .where(calls.c.budget_id == budget_id)
                    .order_by(calls.c.at, calls.c.call_id)
                    .limit(1001)
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1000:
            raise ValueError("budget_history_bound")
        if row["calls_hash"] != self._history_hash(rows):
            raise ValueError("call_history_integrity")
        history = [self._read_call(item) for item in rows]
        return policy, history

    @staticmethod
    def _history_hash(rows: Sequence[RowMapping]) -> str:
        return content_hash(
            [
                dict(
                    call_id=str(row["call_id"]),
                    budget_id=str(row["budget_id"]),
                    at=row["at"].astimezone(UTC).isoformat(),
                    reservation_hash=row["reservation_hash"],
                    result_hash=row["result_hash"],
                )
                for row in rows
            ]
        )

    async def _checkpoint(self, connection: AsyncConnection, budget_id: PyUUID) -> None:
        rows = (
            (
                await connection.execute(
                    select(calls)
                    .where(calls.c.budget_id == budget_id)
                    .order_by(calls.c.at, calls.c.call_id)
                )
            )
            .mappings()
            .all()
        )
        await connection.execute(
            update(budgets)
            .where(budgets.c.budget_id == budget_id)
            .values(calls_hash=self._history_hash(rows))
        )

    @staticmethod
    def _read_call(row: RowMapping) -> tuple[_Call, ProviderResult | None]:
        call = _Call.model_validate(row["reservation"])
        if (
            call.call_id != row["call_id"]
            or call.at != row["at"]
            or content_hash(row["reservation"]) != row["reservation_hash"]
        ):
            raise ValueError("call_integrity")
        result = None if row["result"] is None else ProviderResult.model_validate(row["result"])
        expected = None if result is None else content_hash(row["result"])
        if expected != row["result_hash"]:
            raise ValueError("result_integrity")
        return call, result

    async def reserve(
        self,
        budget_id: PyUUID,
        request: AIRequest,
        price: PriceSnapshot,
        at: datetime,
        run_id: PyUUID,
        run_limit: Decimal,
    ) -> Reservation:
        async with self.engine.begin() as connection:
            policy, history = await self._load(connection, budget_id)
            for call, result in history:
                if call.call_id == request.call_id:
                    # A retry may have a later receipt clock, but no changed request or cap.
                    if (
                        call.request != request
                        or call.price != price
                        or call.run_id != run_id
                        or call.run_limit != run_limit
                    ):
                        raise ValueError("call_identity_immutable")
                    if result is not None:
                        return Reservation(
                            status="completed",
                            reason="recorded_result",
                            reserved_amount=call.reservation,
                            result=result,
                        )
                    return Reservation(
                        status="blocked", reason="call_pending", reserved_amount=call.reservation
                    )
            amount = reserve_cost(request, price, at)
            proposed = _Call(
                call_id=request.call_id,
                request=request,
                price=price,
                run_id=run_id,
                run_limit=run_limit,
                at=at,
                reservation=amount,
            )
            reason = self._admit(policy, history, proposed)
            if reason:
                return Reservation(status="blocked", reason=reason, reserved_amount=amount)
            payload = proposed.model_dump(mode="json")
            await connection.execute(
                insert(calls).values(
                    call_id=request.call_id,
                    budget_id=budget_id,
                    at=proposed.at,
                    reservation=payload,
                    reservation_hash=content_hash(payload),
                    result=None,
                    result_hash=None,
                )
            )
            await self._checkpoint(connection, budget_id)
            return Reservation(status="new", reason="reserved", reserved_amount=amount)

    @staticmethod
    def _admit(
        policy: BudgetPolicy, history: list[tuple[_Call, ProviderResult | None]], proposed: _Call
    ) -> str | None:
        if any(_outcome(call, result)[1] for call, result in history):
            return "unresolved_call"
        if len(history) >= policy.max_calls:
            return "call_limit"
        if history and proposed.at < max(call.at for call, _ in history):
            return "clock_regression"
        if any(
            call.run_id == proposed.run_id and call.run_limit != proposed.run_limit
            for call, _ in history
        ):
            raise ValueError("run_limit_immutable")
        amounts = [(call, _outcome(call, result)[0]) for call, result in history]
        checks = [
            (_total((v for _, v in amounts), Decimal(0)), policy.total_limit),
            (
                _total((v for c, v in amounts if c.at.date() == proposed.at.date()), Decimal(0)),
                policy.daily_limit,
            ),
            (
                _total(
                    (
                        v
                        for c, v in amounts
                        if (c.at.year, c.at.month) == (proposed.at.year, proposed.at.month)
                    ),
                    Decimal(0),
                ),
                policy.monthly_limit,
            ),
            (
                _total((v for c, v in amounts if c.run_id == proposed.run_id), Decimal(0)),
                proposed.run_limit,
            ),
        ]
        for limit in policy.provider_limits:
            if limit.provider == proposed.request.provider:
                checks.append(
                    (
                        _total(
                            (v for c, v in amounts if c.request.provider == limit.provider),
                            Decimal(0),
                        ),
                        limit.limit,
                    )
                )
        if any(_total((used, proposed.reservation)) > limit for used, limit in checks):
            return "spending_limit"
        return None

    async def finish(
        self, budget_id: PyUUID, call_id: PyUUID, request_hash: str, result: ProviderResult
    ) -> ProviderResult:
        async with self.engine.begin() as connection:
            _, history = await self._load(connection, budget_id)
            for call, previous in history:
                if call.call_id != call_id:
                    continue
                if call.request.fingerprint != request_hash:
                    raise ValueError("call_request_mismatch")
                if previous is not None:
                    if previous != result:
                        raise ValueError("call_result_immutable")
                    return previous
                payload = result.model_dump(mode="json")
                await connection.execute(
                    update(calls)
                    .where(calls.c.call_id == call_id)
                    .values(result=payload, result_hash=content_hash(payload))
                )
                await self._checkpoint(connection, budget_id)
                return result
            raise ValueError("call_missing")

    async def costs_for_run(self, budget_id: PyUUID, run_id: PyUUID) -> tuple[RunCost, ...]:
        """Expose verified per-call estimates without promoting uncertain holds to costs."""
        async with self.engine.begin() as connection:
            _, history = await self._load(connection, budget_id)
            output: list[RunCost] = []
            for call, result in history:
                if call.run_id != run_id:
                    continue
                amount, unknown = _outcome(call, result)
                output.append(
                    RunCost(
                        call_id=call.call_id,
                        provider=call.request.provider,
                        at=call.at,
                        amount=None if unknown else amount,
                        held=amount if unknown else Decimal(0),
                    )
                )
            return tuple(output)

    async def summary(self, budget_id: PyUUID) -> dict[str, object]:
        async with self.engine.begin() as connection:
            policy, history = await self._load(connection, budget_id)
            unresolved = sum(_outcome(call, result)[1] for call, result in history)
            spent = _total(
                (_outcome(c, r)[0] for c, r in history if not _outcome(c, r)[1]), Decimal(0)
            )
            held = _total((_outcome(c, r)[0] for c, r in history if _outcome(c, r)[1]), Decimal(0))
            return {
                "budget_id": str(budget_id),
                "currency": policy.currency,
                "total_limit": str(policy.total_limit),
                "spent_estimated": str(spent),
                "reserved": str(held),
                "unresolved": unresolved,
                "call_count": len(history),
                "cost_basis": "usage_estimate_not_invoice",
            }
