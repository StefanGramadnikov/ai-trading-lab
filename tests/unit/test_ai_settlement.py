"""An unknown outcome can be charged at its reserved maximum; the price window is configurable."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_trading_lab.ai.budget import _outcome, reserve_cost, settled
from ai_trading_lab.ai.models import ProviderResult
from ai_trading_lab.config import Settings
from tests.unit.test_ai_budget import AT, call, pricing, request


def test_a_settled_call_is_charged_exactly_its_reservation_and_is_resolved() -> None:
    req = request()
    record = call(req)
    result = settled(req, "timeout_unknown_billing")
    assert result.status == "settled" and result.answer is None
    assert result.reason == "settled_timeout_unknown_billing"
    amount, unresolved = _outcome(record, result)
    assert (amount, unresolved) == (record.reservation, False)
    assert record.reservation == Decimal("0.003")  # 1000 input at 1 + 1000 output at 2 per million


def test_a_settled_result_must_carry_usage() -> None:
    with pytest.raises(ValidationError, match="settled_requires_usage"):
        ProviderResult(status="settled", model="m", reason="x")


def test_the_price_window_defaults_to_one_day_and_can_be_widened_but_never_into_the_future() -> (
    None
):
    req = request()
    old = AT + timedelta(days=3)
    with pytest.raises(ValueError, match="pricing_not_current"):
        reserve_cost(req, pricing(), old)
    assert reserve_cost(req, pricing(), old, timedelta(days=7)) == Decimal("0.003")
    with pytest.raises(ValueError, match="pricing_not_current"):
        reserve_cost(req, pricing(), AT - timedelta(seconds=1), timedelta(days=7))
    with pytest.raises(ValueError, match="pricing_not_current"):
        reserve_cost(req, pricing(), AT + timedelta(days=8), timedelta(days=7))


@pytest.mark.parametrize("days", [0, 31, -1])
def test_the_configured_price_window_is_bounded(days: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_price_max_age_days=days)
    assert Settings(_env_file=None).ai_price_max_age_days == 1
    assert Settings(_env_file=None, ai_price_max_age_days=14).ai_price_max_age_days == 14
