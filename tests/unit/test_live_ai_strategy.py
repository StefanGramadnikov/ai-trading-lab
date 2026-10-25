"""The AI strategy's pure parts: the numeric-only snapshot and the direction-only decisions."""

import json
from datetime import timedelta

import pytest

from ai_trading_lab.live.ai_strategy import Held, build_snapshot
from ai_trading_lab.live.strategies import (
    Mark,
    Proposal,
    decide_ai,
    review_candidates,
)
from tests.unit.test_live_logic import D
from tests.unit.test_live_strategies import (
    CONFIG,
    NOW,
    PAIR,
    flat_candles,
    held,
    loop,
    market,
    portfolio,
)

INTERVAL = timedelta(hours=4)


def numeric(text: str) -> bool:
    try:
        D(text)
    except ArithmeticError:
        return False
    return True


def walk(value: object) -> list[object]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in walk(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in walk(item)]
    return [value]


@pytest.mark.asyncio
async def test_the_snapshot_is_numbers_only_bounded_and_deterministic() -> None:
    m = await market()
    one = build_snapshot(
        pair=PAIR, now=NOW, market=m, candles=flat_candles(), held=None, config=CONFIG
    )
    two = build_snapshot(
        pair=PAIR, now=NOW, market=m, candles=flat_candles(), held=None, config=CONFIG
    )
    assert one is not None and two is not None and one == two
    assert len(one.payload) < 3000 and len(one.evidence_ids) == 1
    body = json.loads(one.payload)
    assert body["label"] == "untrusted_data_only" and body["position"] is None
    # Every leaf is a number, a numeric string, a boolean, null, or one of the three fixed labels.
    allowed_text = {"untrusted_data_only", PAIR}
    for leaf in walk(body):
        if isinstance(leaf, str):
            assert leaf in allowed_text or numeric(leaf), leaf
        else:
            assert leaf is None or isinstance(leaf, bool | int), leaf
    assert len(body["closes_15m_last_4h"]) == 16 and len(body["closes_1h_last_24h"]) == 24
    assert one.instrument_key == m.quote.instrument.key
    assert one.available_at_utc == NOW


@pytest.mark.asyncio
async def test_the_snapshot_reports_a_position_and_changes_with_the_market() -> None:
    m = await market()
    held_ = Held(entry_price=D(100), mark_price=D(104), age_hours=D("5.04"))
    with_position = build_snapshot(
        pair=PAIR, now=NOW, market=m, candles=flat_candles(), held=held_, config=CONFIG
    )
    assert with_position is not None
    position = json.loads(with_position.payload)["position"]
    assert position == {"entry_price": "100", "pnl_pct": "4", "age_hours": "5"}
    other = build_snapshot(
        pair=PAIR,
        now=NOW,
        market=m,
        candles=flat_candles(("100", "90", "95", "25")),
        held=None,
        config=CONFIG,
    )
    flat = build_snapshot(
        pair=PAIR, now=NOW, market=m, candles=flat_candles(), held=None, config=CONFIG
    )
    assert other is not None and flat is not None
    assert other.evidence_ids != flat.evidence_ids and other.snapshot_id != flat.snapshot_id


@pytest.mark.asyncio
async def test_too_little_history_or_a_one_sided_book_produces_no_snapshot() -> None:
    m = await market()
    assert (
        build_snapshot(
            pair=PAIR, now=NOW, market=m, candles=flat_candles()[:50], held=None, config=CONFIG
        )
        is None
    )
    empty = m.quote.model_copy(update={"asks": ()})
    one_sided = type(m)(PAIR, m.entry, empty, m.rules)
    assert (
        build_snapshot(
            pair=PAIR, now=NOW, market=one_sided, candles=flat_candles(), held=None, config=CONFIG
        )
        is None
    )


async def ai(**changes: object):  # type: ignore[no-untyped-def]
    m = await market()
    values: dict[str, object] = {
        "config": CONFIG,
        "now": NOW,
        "markets": {PAIR: m},
        "state": portfolio(m),
        "loop": loop(),
        "marks": {},
        "equity": D(100),
        "blocked": None,
        "proposals": {},
    }
    return decide_ai(**(values | changes))  # type: ignore[arg-type]


def proposal(action: str, reason: str | None = None) -> Proposal:
    return Proposal(
        action=action,
        reason=reason or f"ai_{action}",
        detail={"call_id": "c1", "cost_usd": "0.004"},
    )


@pytest.mark.asyncio
async def test_a_buy_proposal_is_sized_by_the_deterministic_code_and_keeps_its_detail() -> None:
    (decision,) = await ai(proposals={PAIR: proposal("buy")})
    assert (decision.action, decision.reason) == ("buy", "ai_buy")
    assert decision.detail == {"call_id": "c1", "cost_usd": "0.004"}
    order = decision.order
    assert order is not None and order.side == "buy"
    assert order.limit_price == D("67067.2")  # the same marketable limit the rules use
    assert D("19.99") < order.quantity * order.limit_price <= D(20)


@pytest.mark.asyncio
async def test_without_a_buy_proposal_nothing_is_bought() -> None:
    (none,) = await ai()
    assert (none.action, none.reason, none.order) == ("hold", "ai_not_reviewed", None)
    (abstain,) = await ai(proposals={PAIR: proposal("hold", "ai_abstain")})
    assert (abstain.action, abstain.reason) == ("hold", "ai_abstain")
    assert abstain.detail is not None
    (sell,) = await ai(proposals={PAIR: proposal("sell")})
    assert (sell.action, sell.reason) == ("hold", "ai_sell_ignored_flat")
    (failed,) = await ai(proposals={PAIR: proposal("hold", "ai_unavailable_global_spending_limit")})
    assert failed.reason == "ai_unavailable_global_spending_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"blocked": "drawdown_latched"}, "drawdown_latched"),
        ({"blocked": "daily_loss_suspended"}, "daily_loss_suspended"),
        ({"blocked": "command_capacity"}, "command_capacity"),
        ({"equity": None}, "equity_unavailable"),
        ({"markets": {}}, "no_market_data"),
    ],
)
async def test_a_buy_proposal_cannot_override_a_risk_gate_or_missing_data(
    change: dict[str, object], reason: str
) -> None:
    (decision,) = await ai(proposals={PAIR: proposal("buy")}, **change)
    assert (decision.action, decision.reason, decision.order) == ("hold", reason, None)


@pytest.mark.asyncio
async def test_cooldown_and_the_daily_entry_limit_bind_the_ai_too() -> None:
    p = {PAIR: proposal("buy")}
    (cool,) = await ai(proposals=p, loop=loop(last_stop_at={PAIR: NOW - timedelta(hours=2)}))
    assert cool.reason == "cooldown_after_stop"
    (recent,) = await ai(proposals=p, loop=loop(last_entry_at={PAIR: NOW - timedelta(hours=10)}))
    assert recent.reason == "entry_interval"


@pytest.mark.asyncio
async def test_a_buy_proposal_for_a_held_pair_is_ignored_and_a_sell_exits_it() -> None:
    m = await market()
    state = held(m)
    marks = {PAIR: Mark(price=D("67000"), net=D("20"))}
    entered = loop(entered_at={PAIR: NOW - timedelta(hours=1)})
    (buy,) = await ai(state=state, marks=marks, loop=entered, proposals={PAIR: proposal("buy")})
    assert (buy.action, buy.reason) == ("hold", "ai_buy_ignored_held")
    (keep,) = await ai(state=state, marks=marks, loop=entered)
    assert (keep.action, keep.reason) == ("hold", "holding")
    (sell,) = await ai(state=state, marks=marks, loop=entered, proposals={PAIR: proposal("sell")})
    assert (sell.action, sell.reason) == ("sell", "ai_sell")
    order = sell.order
    assert order is not None and order.side == "sell" and order.quantity == D("0.0003")
    assert sell.detail == {"call_id": "c1", "cost_usd": "0.004"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mark", "entered_hours", "reason"),
    [("63650.0", 1, "stop_loss"), ("72360.0", 1, "take_profit"), ("67000.0", 72, "max_hold")],
)
async def test_mechanical_exits_come_first_whatever_the_ai_says_or_whether_it_answered(
    mark: str, entered_hours: int, reason: str
) -> None:
    m = await market()
    for proposals in ({}, {PAIR: proposal("hold", "ai_unavailable_provider_error")}):
        (decision,) = await ai(
            state=held(m),
            marks={PAIR: Mark(price=D(mark), net=D(mark) * D("0.0003"))},
            loop=loop(entered_at={PAIR: NOW - timedelta(hours=entered_hours)}),
            blocked="drawdown_latched",
            equity=None,
            proposals=proposals,
        )
        assert (decision.action, decision.reason) == ("sell", reason)
        assert decision.order is not None and decision.order.quantity == D("0.0003")
        assert decision.detail is None  # the exit was the rules', not the AI's


async def candidates(**changes: object) -> list[str]:
    m = await market()
    values: dict[str, object] = {
        "config": CONFIG,
        "now": NOW,
        "interval": INTERVAL,
        "markets": {PAIR: m},
        "state": portfolio(m),
        "loop": loop(),
        "marks": {},
        "equity": D(100),
        "blocked": None,
    }
    return review_candidates(**(values | changes))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_pair_is_reviewed_when_never_reviewed_or_four_hours_have_passed() -> None:
    assert await candidates() == [PAIR]
    reviewed = loop(last_review_at={PAIR: NOW - timedelta(hours=1)})
    assert await candidates(loop=reviewed) == []
    due = loop(last_review_at={PAIR: NOW - timedelta(hours=4)})
    assert await candidates(loop=due) == [PAIR]
    # Cycle times jitter by seconds and a review must not slip to the next quarter hour.
    near = loop(last_review_at={PAIR: NOW - timedelta(hours=3, minutes=57)})
    assert await candidates(loop=near) == [PAIR]
    almost = loop(last_review_at={PAIR: NOW - timedelta(hours=3, minutes=50)})
    assert await candidates(loop=almost) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"blocked": "drawdown_latched"},
        {"blocked": "command_capacity"},
        {"equity": None},
        {"markets": {}},
        {"loop": loop(last_stop_at={PAIR: NOW - timedelta(hours=1)})},
        {"loop": loop(last_entry_at={PAIR: NOW - timedelta(hours=1)})},
    ],
)
async def test_nothing_is_paid_for_when_the_answer_could_not_change_the_outcome(
    change: dict[str, object],
) -> None:
    assert await candidates(**change) == []


@pytest.mark.asyncio
async def test_a_held_pair_is_reviewed_unless_it_is_already_exiting() -> None:
    m = await market()
    marks = {PAIR: Mark(price=D("67000"), net=D("20"))}
    quiet = loop(entered_at={PAIR: NOW - timedelta(hours=1)})
    assert await candidates(state=held(m), marks=marks, loop=quiet) == [PAIR]
    stopped = {PAIR: Mark(price=D("63650"), net=D("19"))}
    assert await candidates(state=held(m), marks=stopped, loop=quiet) == []
    old = loop(entered_at={PAIR: NOW - timedelta(hours=72)})
    assert await candidates(state=held(m), marks=marks, loop=old) == []


@pytest.mark.asyncio
async def test_stale_candles_produce_no_snapshot() -> None:
    m = await market()
    fresh = flat_candles()
    stale = [
        c.model_copy(
            update={
                "open_time_utc": c.open_time_utc - timedelta(hours=2),
                "close_time_utc": c.close_time_utc - timedelta(hours=2),
            }
        )
        for c in fresh
    ]
    kwargs = {"pair": PAIR, "now": NOW, "market": m, "held": None, "config": CONFIG}
    assert build_snapshot(candles=fresh, **kwargs) is not None  # type: ignore[arg-type]
    assert build_snapshot(candles=stale, **kwargs) is None  # type: ignore[arg-type]
