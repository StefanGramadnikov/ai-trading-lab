"""Live-loop persistence: strategies, candles, decisions (plan-then-finish), state, equity."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.live.risk import RiskState
from ai_trading_lab.live.store import LiveStore, LoopState
from ai_trading_lab.market_data.adapters.kraken import Candle

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
D = Decimal


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


def candle(minutes_ago: int, price: str = "100") -> Candle:
    close = NOW - timedelta(minutes=minutes_ago)
    return Candle(
        open_time_utc=close - timedelta(minutes=15),
        close_time_utc=close,
        interval_minutes=15,
        open=D(price),
        high=D(price) + 1,
        low=D(price) - 1,
        close=D(price),
        vwap=D(price),
        volume=D("2.5"),
        trades=7,
    )


async def test_strategy_registration_is_idempotent_immutable_and_toggleable(
    engine: AsyncEngine,
) -> None:
    store, sid, pid = LiveStore(engine), f"s-{uuid4()}", uuid4()
    await store.register(sid, "rules", pid, {"k": 1}, NOW)
    await store.register(sid, "rules", pid, {"k": 1}, NOW)
    with pytest.raises(ValueError, match="immutable"):
        await store.register(sid, "rules", pid, {"k": 2}, NOW)
    row = next(r for r in await store.strategies() if r.strategy_id == sid)
    assert row.enabled and row.portfolio_id == pid and row.kind == "rules"
    await store.set_enabled(sid, False)
    assert sid not in [r.strategy_id for r in await store.strategies()]
    assert sid in [r.strategy_id for r in await store.strategies(enabled_only=False)]
    with pytest.raises(ValueError, match="unknown"):
        await store.set_enabled("missing-" + sid, True)


async def test_candles_are_stored_once_ordered_and_bounded(engine: AsyncEngine) -> None:
    store, pair = LiveStore(engine), f"T{uuid4().hex[:8].upper()}"
    rows = [candle(15 * n, str(100 + n)) for n in range(5, 0, -1)]
    await store.upsert_candles(pair, rows)
    await store.upsert_candles(pair, rows[:3])  # a repeat fetch changes nothing
    stored = await store.candles(pair, 15, 100)
    assert [c.open_time_utc for c in stored] == sorted(c.open_time_utc for c in stored)
    assert len(stored) == 5 and stored[-1] == max(rows, key=lambda c: c.open_time_utc)
    assert len(await store.candles(pair, 15, 2)) == 2
    assert await store.last_open_time(pair, 15) == stored[-1].open_time_utc
    assert await store.last_open_time("NOPE00000", 15) is None


async def test_decisions_are_planned_before_submission_and_finished_once(
    engine: AsyncEngine,
) -> None:
    store, sid = LiveStore(engine), f"s-{uuid4()}"
    await store.register(sid, "rules", uuid4(), {}, NOW)
    key = f"{sid}:XBTEUR:buy:1"
    planned = await store.plan_decision(
        sid, "XBTEUR", NOW, "buy", "breakout", key, {"intent": 1}, {"evidence": 2}
    )
    assert planned.created and planned.status == "planned"
    again = await store.plan_decision(
        sid, "XBTEUR", NOW, "buy", "breakout", key, {"intent": 1}, {"evidence": 2}
    )
    assert not again.created and again.decision_id == planned.decision_id
    assert [p.decision_id for p in await store.planned(sid)] == [planned.decision_id]
    await store.finish_decision(planned.decision_id, "filled", {"quantity": "0.1"}, NOW)
    assert await store.planned(sid) == []
    with pytest.raises(ValueError, match="finished"):
        await store.finish_decision(planned.decision_id, "rejected", {}, NOW)
    await store.log_decision(sid, "ETHEUR", NOW, "hold", "no_breakout")
    history = await store.decisions(sid)
    assert [(d.pair, d.action, d.status) for d in history] == [
        ("XBTEUR", "buy", "filled"),
        ("ETHEUR", "hold", "skipped"),
    ]


async def test_loop_state_and_equity_roundtrip(engine: AsyncEngine) -> None:
    store, sid = LiveStore(engine), f"s-{uuid4()}"
    await store.register(sid, "rules", uuid4(), {}, NOW)
    assert await store.load_state(sid) is None
    state = LoopState(
        risk=RiskState(peak_equity=D(100), day=date(2026, 1, 10), day_open_equity=D(100)),
        entered_at={"XBTEUR": NOW},
        last_entry_at={"XBTEUR": NOW},
        last_stop_at={},
    )
    await store.save_state(sid, state, NOW)
    assert await store.load_state(sid) == state
    await store.record_equity(sid, NOW, D("101.5"), D("80"), D("21.5"))
    await store.record_equity(sid, NOW, D("101.5"), D("80"), D("21.5"))  # same cycle: no duplicate
    points = await store.equity_curve(sid)
    assert [(p.at_utc, p.equity) for p in points] == [(NOW, D("101.5"))]


async def test_recent_reads_return_the_newest_rows_in_time_order_with_a_true_count(
    engine: AsyncEngine,
) -> None:
    store, sid = LiveStore(engine), f"s-{uuid4()}"
    await store.register(sid, "rules", uuid4(), {}, NOW)
    for n in range(12):
        await store.log_decision(sid, "XBTEUR", NOW + timedelta(minutes=n), "hold", f"r{n}")
        await store.record_equity(sid, NOW + timedelta(minutes=n), D(100 + n), D(100), D(n))
    assert await store.decision_count(sid) == 12
    recent = await store.recent_decisions(sid, 3)
    assert [d.reason for d in recent] == ["r9", "r10", "r11"]
    curve = await store.equity_curve(sid, limit=4)
    assert [p.equity for p in curve] == [D(108), D(109), D(110), D(111)]


async def test_decision_detail_roundtrips_for_holds_and_planned_orders(
    engine: AsyncEngine,
) -> None:
    store, sid = LiveStore(engine), f"detail-{uuid4().hex[:6]}"
    detail = {"call_id": "c-1", "cost_usd": "0.004", "answer": {"action": "buy"}}
    await store.log_decision(sid, "XBTEUR", NOW, "hold", "ai_abstain", detail)
    await store.log_decision(sid, "ETHEUR", NOW, "hold", "no_breakout")
    planned = await store.plan_decision(
        sid, "SOLEUR", NOW, "buy", "ai_buy", "k1", {"i": 1}, {"e": 1}, detail
    )
    assert planned.created
    rows = {d.pair: d for d in await store.decisions(sid)}
    assert rows["XBTEUR"].detail == detail and rows["SOLEUR"].detail == detail
    assert rows["ETHEUR"].detail is None
    assert [d.detail for d in await store.recent_decisions(sid, 3)] == [detail, None, detail]


async def test_loop_state_saved_before_reviews_existed_still_loads(engine: AsyncEngine) -> None:
    store, sid = LiveStore(engine), f"old-{uuid4().hex[:6]}"
    state = LoopState(
        risk=RiskState(peak_equity=D(100), day=date(2026, 1, 10), day_open_equity=D(100))
    )
    await store.save_state(sid, state, NOW)
    loaded = await store.load_state(sid)
    assert loaded is not None and loaded.last_review_at == {}
    reviewed = state.model_copy(update={"last_review_at": {"XBTEUR": NOW}})
    await store.save_state(sid, reviewed, NOW)
    again = await store.load_state(sid)
    assert again is not None and again.last_review_at == {"XBTEUR": NOW}


async def test_toggles_are_journaled_atomically_idempotent_and_newest_first(
    engine: AsyncEngine,
) -> None:
    store, sid, other = LiveStore(engine), f"a-{uuid4()}", f"b-{uuid4()}"
    for strategy in (sid, other):
        await store.register(strategy, "rules", uuid4(), {"k": 1}, NOW)
    assert await store.set_enabled(sid, False, actor="cli:me", reason="too noisy", at=NOW) is True
    assert await store.set_enabled(sid, False, actor="cli:me", reason="again", at=NOW) is False
    assert await store.set_enabled(sid, True, actor="cli:me", reason="back", at=NOW) is True
    await store.set_enabled(other, False, actor="cli:me", reason="x", at=NOW)
    rows = await store.audit(sid)
    assert [(r.action, r.enabled, r.reason, r.actor) for r in rows] == [
        ("enable", True, "back", "cli:me"),
        ("disable", False, "too noisy", "cli:me"),
    ]  # the repeated disable left no entry, and the other strategy's change is not mixed in
    assert rows[0].seq > rows[1].seq and rows[0].at_utc == NOW
    assert len(await store.audit(limit=1)) == 1
    long = await store.set_enabled(sid, False, actor="a" * 300, reason="r" * 500)
    assert long is True
    latest = (await store.audit(sid, 1))[0]
    assert len(latest.actor) == 100 and len(latest.reason) == 200
    with pytest.raises(ValueError, match="unknown"):
        await store.set_enabled("missing-" + sid, True, actor="x", reason="x")
    assert all(r.strategy_id != "missing-" + sid for r in await store.audit())
