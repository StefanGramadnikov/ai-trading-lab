"""AI spend and breaker state, the verdict rule and curve thinning, without a database."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from ai_trading_lab.config import Settings
from ai_trading_lab.dashboard.queries import DashboardData, _downsample
from ai_trading_lab.live.config import LiveConfig  # noqa: F401  (documents the 100 EUR start)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class FakeBudget:
    def __init__(self, spent: str, held: str, times: list[datetime]) -> None:
        self.ident = uuid4()
        self.spent, self.held, self.times = spent, held, times

    async def budget_ids(self) -> list[Any]:
        return [self.ident]

    async def summary(self, budget_id: Any, *, snapshot: Any = None) -> dict[str, object]:
        assert snapshot is not None  # the page must never take the locking read
        return {"spent_estimated": self.spent, "reserved": self.held, "call_count": 7}

    async def settled_times(self, budget_id: Any, since: datetime) -> list[datetime]:
        return [t for t in self.times if t >= since]


def make(budget: FakeBudget, **settings: Any) -> DashboardData:
    engine = MagicMock()

    @asynccontextmanager
    async def connect() -> Any:
        yield MagicMock()

    engine.connect = connect
    data = DashboardData(
        engine,
        Settings(**settings),
        clock=lambda: NOW,
    )
    data.budget = budget  # type: ignore[assignment]
    return data


ON = {"ai_enabled": True, "ai_total_limit_usd": Decimal(15)}


@pytest.mark.asyncio
async def test_the_ai_is_off_until_enabled_with_a_cap() -> None:
    data = make(FakeBudget("0", "0", []))
    assert (await data.ai(NOW))["state"] == "off"


@pytest.mark.asyncio
async def test_spend_is_reported_against_the_cap_with_the_eighty_percent_warning() -> None:
    low = await make(FakeBudget("5.00", "0", []), **ON).ai(NOW)
    assert (low["state"], low["used_fraction"]) == ("ok", "0.3333")
    warn = await make(FakeBudget("11.00", "1.00", []), **ON).ai(NOW)
    assert (warn["state"], warn["used_fraction"]) == ("budget_warning", "0.8000")
    gone = await make(FakeBudget("15.00", "0", []), **ON).ai(NOW)
    assert gone["state"] == "budget_exhausted"
    assert gone["breaker_open"] is False and gone["breaker_resumes_at"] is None


@pytest.mark.asyncio
async def test_the_breaker_resumes_when_the_oldest_of_the_failures_falls_out_of_24_hours() -> None:
    times = [NOW - timedelta(hours=h) for h in (20, 15, 10, 5, 1)]  # five unknown outcomes
    state = await make(FakeBudget("1", "0", sorted(times)), **ON).ai(NOW)
    assert state["breaker_open"] is True and state["state"] == "breaker_open"
    # After the oldest (20 h ago) is 24 h old, only four remain: the AI may ask again.
    assert (
        state["breaker_resumes_at"] == (NOW - timedelta(hours=20) + timedelta(days=1)).isoformat()
    )
    assert state["unknown_outcomes_24h"] == 5


@pytest.mark.asyncio
async def test_four_unknown_outcomes_do_not_open_the_breaker() -> None:
    times = [NOW - timedelta(hours=h) for h in (20, 15, 10, 5)]
    state = await make(FakeBudget("1", "0", sorted(times)), **ON).ai(NOW)
    assert (state["breaker_open"], state["breaker_resumes_at"]) == (False, None)


@pytest.mark.asyncio
async def test_an_exhausted_budget_outranks_the_breaker_in_the_state_label() -> None:
    times = [NOW - timedelta(hours=h) for h in (5, 4, 3, 2, 1)]
    state = await make(FakeBudget("15", "0", times), **ON).ai(NOW)
    assert state["state"] == "budget_exhausted"


def standings(*equities: str) -> list[dict[str, Any]]:
    return [{"strategy_id": f"s{i}", "label": f"S{i}", "equity": e} for i, e in enumerate(equities)]


def test_the_verdict_stays_too_early_for_a_young_run_however_big_the_gap() -> None:
    verdict = DashboardData._verdict(standings("120", "100"), timedelta(hours=71))
    assert (verdict["state"], verdict["why"]) == ("too_early", "run_under_72h")
    assert [s["strategy_id"] for s in verdict["standings"]] == ["s0", "s1"]


def test_a_close_race_is_too_early_even_in_an_old_run() -> None:
    verdict = DashboardData._verdict(standings("100.50", "100"), timedelta(days=5))
    assert (verdict["state"], verdict["why"], verdict["gap_eur"]) == (
        "too_early",
        "gap_under_1_eur",
        "0.50",
    )


def test_an_old_run_with_a_clear_gap_names_the_leader() -> None:
    verdict = DashboardData._verdict(standings("98", "103.40", "101"), timedelta(days=5))
    assert (verdict["state"], verdict["leader"], verdict["gap_eur"]) == ("leader", "s1", "2.40")
    assert [s["strategy_id"] for s in verdict["standings"]] == ["s1", "s2", "s0"]


def test_a_single_strategy_or_no_history_is_no_data() -> None:
    assert DashboardData._verdict(standings("100"), timedelta(days=9))["why"] == "no_data"
    assert DashboardData._verdict(standings("100", "99"), None)["why"] == "no_data"


@pytest.mark.parametrize("size", [0, 1, 399, 400])
def test_short_curves_are_untouched(size: int) -> None:
    values = list(range(size))
    assert _downsample(values, 400) == values


def test_long_curves_keep_their_first_and_last_points() -> None:
    values = list(range(5000))
    thinned = _downsample(values, 400)
    assert len(thinned) == 400 and thinned[0] == 0 and thinned[-1] == 4999
