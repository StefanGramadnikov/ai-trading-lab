"""Synthetic requests exercise conservative cost and envelope admission boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_trading_lab.ai.budget import (
    BudgetPolicy,
    BudgetStore,
    ProviderLimit,
    _Call,
    _outcome,
    reserve_cost,
)
from ai_trading_lab.ai.models import (
    AIRequest,
    Evidence,
    ModelAnswer,
    PriceSnapshot,
    ProviderResult,
    Usage,
)

AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


def request() -> AIRequest:
    return AIRequest(
        call_id=uuid4(),
        provider="openai",
        model="test-model",
        role="strategy",
        evidence=Evidence(
            instrument_key="a" * 64,
            snapshot_id=uuid4(),
            available_at_utc=AT,
            evidence_ids=("snapshot",),
            payload='{"fixture":true}',
        ),
        max_input_tokens=1000,
        max_output_tokens=1000,
        timeout_seconds=5,
    )


def pricing() -> PriceSnapshot:
    return PriceSnapshot(
        provider="openai",
        model="test-model",
        input_per_million="1",
        output_per_million="2",
        source_url="https://example.test/fixture-pricing",
        verified_at_utc=AT,
        version="synthetic-test-v1",
    )


def policy(**changes: object) -> BudgetPolicy:
    values: dict[str, object] = dict(
        budget_id=uuid4(),
        version="test-v1",
        total_limit="1",
        daily_limit="1",
        monthly_limit="1",
        max_calls=100,
    )
    return BudgetPolicy.model_validate(values | changes)


def completed(req: AIRequest, **changes: object) -> ProviderResult:
    data: dict[str, object] = dict(
        status="completed",
        answer=ModelAnswer(
            action="abstain",
            instrument_key=req.evidence.instrument_key,
            limit_price=None,
            reason="synthetic",
            evidence_ids=("snapshot",),
        ),
        usage=Usage(input_tokens=100, output_tokens=100),
        model=req.model,
        reason="completed",
    )
    return ProviderResult.model_validate(data | changes)


def call(req: AIRequest | None = None) -> _Call:
    req = req or request()
    return _Call(
        call_id=req.call_id,
        request=req,
        price=pricing(),
        run_id=uuid4(),
        run_limit="1",
        at=AT,
        reservation="0.003",
    )


def test_cost_exact_discount_not_assumed() -> None:
    req = request()
    assert reserve_cost(req, pricing(), AT) == Decimal("0.003")
    record = call(req)
    result = completed(
        req, usage=Usage(input_tokens=100, output_tokens=100, cached_input_tokens=100)
    )
    assert _outcome(record, result) == (Decimal("0.0003"), False)
    assert _outcome(record, None) == (Decimal("0.003"), True)
    assert _outcome(record, ProviderResult(status="unknown", model=req.model, reason="lost")) == (
        Decimal("0.003"),
        True,
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(provider="xai"),
        dict(model="wrong"),
        dict(verified_at_utc=AT + timedelta(seconds=1)),
        dict(verified_at_utc=AT - timedelta(days=1, microseconds=1)),
        dict(input_per_million=Decimal("0"), output_per_million=Decimal("0")),
    ],
)
def test_price_mismatch_or_unknown_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        reserve_cost(request(), pricing().model_copy(update=change), AT)


def test_naive_clock_and_duplicate_provider_limits() -> None:
    with pytest.raises(ValueError, match="aware"):
        reserve_cost(request(), pricing(), AT.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="duplicate"):
        policy(
            provider_limits=(
                ProviderLimit(provider="openai", limit="1"),
                ProviderLimit(provider="openai", limit="2"),
            )
        )
    with pytest.raises(ValidationError):
        policy(currency="EUR")


@pytest.mark.parametrize("field", ["total_limit", "daily_limit", "monthly_limit"])
def test_each_envelope_limit(field: str) -> None:
    record = call()
    assert BudgetStore._admit(policy(**{field: "0.002"}), [], record) == "spending_limit"
    assert BudgetStore._admit(policy(**{field: "0.003"}), [], record) is None


def test_provider_run_count_and_unresolved_limits() -> None:
    record = call()
    assert (
        BudgetStore._admit(
            policy(provider_limits=(ProviderLimit(provider="openai", limit="0.001"),)), [], record
        )
        == "spending_limit"
    )
    assert (
        BudgetStore._admit(policy(), [], record.model_copy(update={"run_limit": Decimal("0.002")}))
        == "spending_limit"
    )
    assert BudgetStore._admit(policy(), [(record, None)], call()) == "unresolved_call"
    assert (
        BudgetStore._admit(policy(max_calls=1), [(record, completed(record.request))], call())
        == "call_limit"
    )
    assert (
        BudgetStore._admit(
            policy(),
            [(record, completed(record.request))],
            call().model_copy(update={"at": AT - timedelta(seconds=1)}),
        )
        == "clock_regression"
    )
    with pytest.raises(ValueError, match="run_limit_immutable"):
        BudgetStore._admit(
            policy(),
            [(record, completed(record.request))],
            call().model_copy(update={"run_id": record.run_id, "run_limit": Decimal("0.5")}),
        )


def test_overrun_retains_estimate_and_blocks() -> None:
    record = call()
    amount, unresolved = _outcome(
        record, completed(record.request, usage=Usage(input_tokens=2000, output_tokens=2000))
    )
    assert amount == Decimal("0.006") and unresolved
    assert _outcome(record, completed(record.request, model="wrong"))[1]


def test_utc_day_and_month_not_machine_zone() -> None:
    first = call()
    result = completed(first.request)
    near = first.model_copy(update={"at": datetime(2026, 10, 31, 23, 59, tzinfo=UTC)})
    next_call = call().model_copy(update={"at": datetime(2026, 11, 1, tzinfo=UTC)})
    limits = policy(daily_limit="0.003", monthly_limit="0.003", total_limit="0.004")
    assert BudgetStore._admit(limits, [(near, result)], next_call) is None
    assert (
        BudgetStore._admit(
            limits.model_copy(update={"total_limit": Decimal("0.003")}), [(near, result)], next_call
        )
        == "spending_limit"
    )


def test_invalid_reservation_and_expired_price() -> None:
    record = call()
    with pytest.raises(ValidationError, match="integrity"):
        _Call.model_validate(record.model_dump() | {"reservation": "0.001"})
    with pytest.raises(ValidationError):
        _Call.model_validate(record.model_dump() | {"call_id": uuid4()})


def test_cost_addition_preserves_sub_cent_boundary_at_large_limits() -> None:
    from ai_trading_lab.ai.budget import _total

    assert _total((Decimal("1000000000000000000"), Decimal("0.000000000000000001"))) > Decimal(
        "1000000000000000000"
    )


def test_uncertain_usage_overrun_holds_larger_known_amount() -> None:
    record = call()
    assert _outcome(
        record,
        completed(
            record.request,
            status="unknown",
            answer=None,
            usage=Usage(input_tokens=2000, output_tokens=2000),
        ),
    ) == (Decimal("0.006"), True)
