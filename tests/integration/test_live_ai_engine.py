"""The AI strategy end to end: a stub model behind the real budget guard, ledger and journal."""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.ai.budget import BudgetStore, budgets, calls
from ai_trading_lab.ai.models import (
    AIRequest,
    ModelAnswer,
    PriceSnapshot,
    ProviderResult,
    Usage,
)
from ai_trading_lab.config import Settings
from ai_trading_lab.live.ai_strategy import AIConfig, AIDeps
from ai_trading_lab.live.engine import CycleReport, LiveEngine
from ai_trading_lab.live.status import summary
from ai_trading_lab.live.store import DecisionRow, LiveStore, live_candles
from ai_trading_lab.paper_execution.accounting import PortfolioState
from ai_trading_lab.paper_execution.store import PaperStore
from tests.contract.test_kraken import RECEIVED
from tests.integration.test_live_engine import CONFIG, PAIRS, Venue, flat_history

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
D = Decimal


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    async with value.begin() as connection:
        await connection.execute(delete(live_candles))
        # The global cap counts every envelope in the database: start without any history.
        await connection.execute(delete(calls))
        await connection.execute(delete(budgets))
    try:
        yield value
    finally:
        async with value.begin() as connection:
            await connection.execute(delete(calls))
            await connection.execute(delete(budgets))
        await value.dispose()


class Stub:
    """A model that decides by pair from the numbers it is shown, and records every request."""

    def __init__(
        self,
        decide: Callable[[dict[str, object]], str] = lambda _body: "abstain",
        usage: tuple[int, int] = (1000, 100),
    ) -> None:
        self.decide, self.usage = decide, usage
        self.requests: list[AIRequest] = []

    async def analyze(self, request: AIRequest) -> ProviderResult:
        self.requests.append(request)
        action = self.decide(json.loads(request.evidence.payload))
        if action == "error":
            return ProviderResult(
                status="error", model=request.model, reason="http_error_unknown_billing"
            )
        answer = ModelAnswer(
            action=action,
            instrument_key=request.evidence.instrument_key,
            limit_price="1" if action in ("buy", "sell") else None,
            reason="test answer",
            evidence_ids=request.evidence.evidence_ids,
        )
        return ProviderResult(
            status="completed",
            answer=answer,
            usage=Usage(input_tokens=self.usage[0], output_tokens=self.usage[1]),
            model=request.model,
            reason="validated",
            response_id="resp-1",
            response_hash="a" * 64,
        )


class AIRig:
    def __init__(
        self,
        engine: AsyncEngine,
        stub: Stub,
        *,
        provider: str = "fixture",
        settings: Settings | None = None,
        reviewed: bool = True,
    ) -> None:
        self.engine, self.stub, self.venue = engine, stub, Venue()
        self.session = f"t{uuid4().hex[:8]}"
        self.now = RECEIVED + timedelta(seconds=1)
        self.store, self.paper = LiveStore(engine), PaperStore(engine)
        model = "gpt-test-model" if provider == "openai" else "fixture-model"
        paid = provider == "openai"
        self.price = PriceSnapshot(
            provider=provider,
            model=model,
            input_per_million=D("2.5") if paid else D(0),
            output_per_million=D("12") if paid else D(0),
            source_url="fixture://synthetic",
            verified_at_utc=self.now - timedelta(hours=1),
            version="synthetic-v1",
        )
        if settings is None:
            settings = Settings(_env_file=None)
        if paid and reviewed:
            settings = settings.model_copy(update={"ai_reviewed_prices": (self.price,)})
        config = AIConfig(provider=provider, model=model)
        deps = AIDeps(
            provider=stub, price=self.price, budget=BudgetStore(engine, settings), config=config
        )

        @asynccontextmanager
        async def factory() -> AsyncIterator[Venue]:
            yield self.venue

        self.live = LiveEngine(
            engine, factory, CONFIG, clock=lambda: self.now, session=self.session, ai=deps
        )

    async def start(self) -> "AIRig":
        for pair in PAIRS:
            await self.store.upsert_candles(pair, flat_history(200))
        await self.live.setup()
        return self

    async def cycle(self, minutes_later: int = 0) -> CycleReport:
        if minutes_later:
            self.now += timedelta(minutes=minutes_later)
            self.venue.shift += minutes_later * 60
        return await self.live.run_cycle()

    async def portfolio(self, strategy_id: str | None = None) -> PortfolioState:
        wanted = strategy_id or self.live.ai_id
        row = next(
            r for r in await self.store.strategies(enabled_only=False) if r.strategy_id == wanted
        )
        return await self.paper.get(row.portfolio_id)

    async def decisions(self) -> list[DecisionRow]:
        return await self.store.decisions(self.live.ai_id)


async def start(engine: AsyncEngine, stub: Stub, **options: object) -> AIRig:
    return await AIRig(engine, stub, **options).start()  # type: ignore[arg-type]


async def test_without_ai_dependencies_the_ai_strategy_does_not_exist(engine: AsyncEngine) -> None:
    from tests.integration.test_live_engine import rig

    plain = await rig(engine)
    assert plain.live.ai_id not in plain.live.strategy_ids
    assert plain.live.strategy_ids == {plain.rules_id, plain.bench_id}
    assert all(s.strategy_id != plain.live.ai_id for s in await plain.store.strategies())


async def test_an_ai_buy_is_sized_by_the_rules_and_every_cost_is_journaled(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda body: "buy" if body["pair"] != "SOLUSD" else "abstain")
    r = await start(engine, stub)
    report = await r.cycle()
    assert report.errors == [] and len(stub.requests) == 3
    state = await r.portfolio()
    assert len(state.positions) == 2 and state.fees > 0
    for position in state.positions:
        assert D("19.9") < position.cost_basis <= D(20)  # 20% of 100, whatever the model said
    by_pair = {d.pair: d for d in await r.decisions()}
    assert (by_pair["XBTUSD"].action, by_pair["XBTUSD"].reason) == ("buy", "ai_buy")
    assert by_pair["XBTUSD"].status == "filled"
    assert (by_pair["SOLUSD"].action, by_pair["SOLUSD"].reason) == ("hold", "ai_abstain")
    # Each decision carries the call behind it, its answer, usage and cost.
    detail = by_pair["XBTUSD"].detail
    assert detail is not None and detail["flow"] == "completed" and detail["model"]
    assert detail["usage"] == {"input_tokens": 1000, "output_tokens": 100}
    answer = detail["answer"]
    assert isinstance(answer, dict) and answer["action"] == "buy"
    # The call is in the budget ledger under the same id.
    async with engine.connect() as connection:
        ids = set((await connection.execute(select(calls.c.call_id))).scalars().all())
    assert {str(i) for i in ids} >= {str(d.detail["call_id"]) for d in by_pair.values() if d.detail}
    # The AI trades its own portfolio, not the rules strategy's.
    assert (await r.portfolio()).portfolio_id != (await r.portfolio(r.live.rules_id)).portfolio_id


async def test_a_pair_is_reviewed_again_only_after_four_hours(engine: AsyncEngine) -> None:
    stub = Stub(lambda _body: "abstain")
    r = await start(engine, stub)
    await r.cycle()
    assert len(stub.requests) == 3
    await r.cycle(minutes_later=15)
    await r.cycle(minutes_later=30)
    assert len(stub.requests) == 3  # nothing more was asked or paid for
    holds = [d.reason for d in await r.decisions()]
    assert holds.count("ai_not_reviewed") == 6 and holds.count("ai_abstain") == 3
    await r.cycle(minutes_later=225)  # 4 h after the first review
    assert len(stub.requests) == 6


async def test_an_ai_sell_proposal_closes_the_position(engine: AsyncEngine) -> None:
    stub = Stub(lambda body: "buy" if stub.requests and len(stub.requests) <= 3 else "sell")
    r = await start(engine, stub)
    await r.cycle()
    assert len((await r.portfolio()).positions) == 2
    await r.cycle(minutes_later=240)
    state = await r.portfolio()
    assert state.positions == ()
    sells = [d for d in await r.decisions() if d.action == "sell"]
    assert {d.reason for d in sells} == {"ai_sell"} and all(d.detail for d in sells)


async def test_an_unavailable_model_holds_and_is_not_retried_every_cycle(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda _body: "error")
    r = await start(engine, stub)
    report = await r.cycle()
    assert report.errors == [] and len(stub.requests) == 3
    assert (await r.portfolio()).positions == ()
    reasons = {d.reason for d in await r.decisions()}
    assert reasons == {"ai_unavailable_settled_http_error_unknown_billing"}
    await r.cycle(minutes_later=15)
    assert len(stub.requests) == 3
    # Each unknown outcome was charged at its reserved maximum, and the envelope is still usable.
    budget = r.live.proposer.deps.budget  # type: ignore[union-attr]
    report_ = await budget.summary(r.live.proposer.budget_id)  # type: ignore[union-attr]
    assert report_["unresolved"] == 0 and report_["call_count"] == 3
    assert D(str(report_["spent_estimated"])) == 0  # a zero-priced fixture model


async def test_repeated_unknown_outcomes_pause_the_ai_until_the_day_passes(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda _body: "error")
    # The zero-priced fixture price was verified an hour before the first cycle; allow a week.
    r = await start(engine, stub, settings=Settings(_env_file=None, ai_price_max_age_days=7))
    await r.cycle()  # three unknown outcomes
    await r.cycle(minutes_later=240)  # two more, then the breaker
    assert len(stub.requests) == 5
    reasons = [d.reason for d in await r.decisions()]
    assert reasons.count("ai_unavailable_failure_breaker") == 1
    await r.cycle(minutes_later=240)
    assert len(stub.requests) == 5  # still within the day: nothing more is sent
    await r.cycle(minutes_later=1500)  # a day after the first failures
    assert len(stub.requests) > 5


async def test_a_call_a_crash_left_unfinished_is_settled_and_does_not_freeze_the_ai(
    engine: AsyncEngine,
) -> None:
    from ai_trading_lab.ai.models import Evidence
    from ai_trading_lab.live.ai_strategy import PENDING_AFTER

    stub = Stub(lambda _body: "abstain")
    r = await start(engine, stub)
    proposer = r.live.proposer
    assert proposer is not None
    request = AIRequest(
        call_id=uuid4(),
        provider="fixture",
        model="fixture-model",
        role="strategy",
        evidence=Evidence(
            instrument_key="a" * 64,
            snapshot_id=uuid4(),
            available_at_utc=r.now,
            evidence_ids=("snapshot:1",),
            payload="{}",
        ),
        max_input_tokens=1000,
        max_output_tokens=100,
        timeout_seconds=5,
    )
    reserved = await proposer.deps.budget.reserve(
        proposer.budget_id, request, r.price, r.now, proposer.run_id, proposer.policy.total_limit
    )
    assert reserved.status == "new"  # reserved, never finished: the process "died"
    blocked = await proposer.deps.budget.reserve(
        proposer.budget_id,
        request.model_copy(update={"call_id": uuid4()}),
        r.price,
        r.now,
        proposer.run_id,
        proposer.policy.total_limit,
    )
    assert blocked.reason == "unresolved_call"
    await r.cycle(minutes_later=int(PENDING_AFTER.total_seconds() // 60) + 1)
    assert len(stub.requests) == 3  # not frozen: the stale call was settled first


async def test_a_stop_loss_still_exits_while_the_model_is_down(engine: AsyncEngine) -> None:
    def decide(_body: dict[str, object]) -> str:
        if len(stub.requests) > 3:
            raise RuntimeError("model down")
        return "buy"

    stub = Stub(decide)
    r = await start(engine, stub)
    await r.cycle()
    assert len((await r.portfolio()).positions) == 2  # the third buy has no room left
    r.venue.scale = D("0.9")  # a 10% drop, past the 5% stop
    # Four hours later every pair is due for a review (and the model would raise if asked).
    report = await r.cycle(minutes_later=240)
    assert report.errors == []
    assert (await r.portfolio()).positions == ()
    stops = [d for d in await r.decisions() if d.reason == "stop_loss"]
    assert len(stops) == 2 and all(d.status == "filled" for d in stops)
    # The exits went out first; the 10% loss then suspended entries, so nothing more was asked.
    assert len(stub.requests) == 3


async def test_the_spending_cap_stops_paid_calls_before_they_are_sent(
    engine: AsyncEngine,
) -> None:
    # Each call reserves 6000 input and 400 output tokens (about USD 0.0198) and reports that.
    stub = Stub(lambda _body: "abstain", usage=(6000, 400))
    settings = Settings(_env_file=None, ai_total_limit_usd=D("0.05"))
    r = await start(engine, stub, provider="openai", settings=settings)
    report = await r.cycle()
    assert report.errors == []
    assert len(stub.requests) == 2  # a third would cross the cap, so it was never sent
    reasons = sorted(d.reason for d in await r.decisions())
    assert reasons == [
        "ai_abstain",
        "ai_abstain",
        "ai_unavailable_global_spending_limit",
    ]
    later = await r.cycle(minutes_later=240)
    assert later.errors == [] and len(stub.requests) == 2  # still capped
    assert r.live.proposer is not None
    totals = await BudgetStore(engine, settings).summary(r.live.proposer.budget_id)
    assert D(str(totals["spent_estimated"])) == D("0.0396")  # two calls, 6000 in and 400 out each
    assert D(str(totals["spent_estimated"])) <= D("0.05")


async def test_paid_calls_are_refused_while_spending_is_disabled_or_the_price_unreviewed(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda _body: "buy")
    disabled = await start(engine, stub, provider="openai", settings=Settings(_env_file=None))
    await disabled.cycle()
    assert stub.requests == [] and (await disabled.portfolio()).positions == ()
    assert {d.reason for d in await disabled.decisions()} == {
        "ai_unavailable_paid_spending_disabled"
    }
    other = Stub(lambda _body: "buy")
    unreviewed = await start(
        engine,
        other,
        provider="openai",
        settings=Settings(_env_file=None, ai_total_limit_usd=D(10)),
        reviewed=False,
    )
    await unreviewed.cycle()
    assert other.requests == [] and (await unreviewed.portfolio()).positions == ()
    assert {d.reason for d in await unreviewed.decisions()} == {
        "ai_unavailable_pricing_not_reviewed"
    }


async def test_a_stale_price_blocks_the_call_unless_the_owner_widened_the_window(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda _body: "abstain")
    settings = Settings(_env_file=None, ai_total_limit_usd=D(10))
    r = await start(engine, stub, provider="openai", settings=settings)
    await r.cycle()
    assert len(stub.requests) == 3
    # Three days later the one-day default no longer accepts the verification.
    await r.cycle(minutes_later=3 * 24 * 60)
    assert len(stub.requests) == 3
    reasons = {d.reason for d in await r.decisions()}
    assert "ai_unavailable_pricing_not_current" in reasons
    wider = settings.model_copy(update={"ai_price_max_age_days": 7})
    again = await start(engine, Stub(lambda _body: "abstain"), provider="openai", settings=wider)
    await again.cycle(minutes_later=3 * 24 * 60)
    assert len(again.stub.requests) == 3


async def test_status_lists_the_ai_strategy_with_its_reasoning(engine: AsyncEngine) -> None:
    r = await start(engine, Stub(lambda body: "buy" if body["pair"] == "XBTUSD" else "abstain"))
    await r.cycle()
    out = await summary(r.live)
    ai = next(s for s in out["strategies"] if s["strategy_id"] == r.live.ai_id)
    assert ai["kind"] == "ai" and len(ai["positions"]) == 1
    assert any(d["ai"] and d["ai"]["answer"]["action"] == "buy" for d in ai["recent_decisions"])


async def test_a_review_that_was_never_put_is_asked_again_next_cycle(engine: AsyncEngine) -> None:
    stub = Stub(lambda _body: "abstain")
    r = await start(engine, stub)
    r.venue.time_down = False
    stale = {p: flat_history(200, end_open_offset=-9000) for p in PAIRS}
    for pair, rows in stale.items():
        await r.store.upsert_candles(pair, rows)
    await r.store.upsert_candles("XBTUSD", [])  # no-op: keep history as seeded
    # Replace the seeded fresh history with an old one by clearing it first.
    async with engine.begin() as connection:
        await connection.execute(delete(live_candles))
    for pair, rows in stale.items():
        await r.store.upsert_candles(pair, rows)
    r.venue.rows = []  # the venue returns no new candles
    first = await r.cycle()
    assert first.errors == [] and stub.requests == []
    reasons = {d.reason for d in await r.decisions()}
    assert reasons == {"ai_unavailable_no_recent_candles"}
    # Fresh candles arrive: the pairs are asked at once, not four hours later.
    r.venue.rows = None
    await r.cycle(minutes_later=15)
    assert len(stub.requests) == 3


async def test_a_disabled_ai_strategy_asks_nothing_even_for_a_held_position(
    engine: AsyncEngine,
) -> None:
    stub = Stub(lambda _body: "buy")
    r = await start(engine, stub)
    await r.cycle()
    asked = len(stub.requests)
    assert asked == 3 and len((await r.portfolio()).positions) == 2
    await r.store.set_enabled(r.live.ai_id, False, actor="test", reason="cost")
    # Four hours on, with no loss and positions held: every pair is due, yet nothing is asked.
    report = await r.cycle(minutes_later=240)
    assert report.errors == [] and report.strategies[r.live.ai_id].blocked == "strategy_disabled"
    assert len(stub.requests) == asked
    assert len((await r.portfolio()).positions) == 2  # kept: no exit condition was met
    await r.store.set_enabled(r.live.ai_id, True, actor="test", reason="back")
    await r.cycle(minutes_later=15)
    assert len(stub.requests) > asked  # reviews resume once it is on again


async def test_a_disabled_ai_strategy_still_stops_out(engine: AsyncEngine) -> None:
    stub = Stub(lambda _body: "buy")
    r = await start(engine, stub)
    await r.cycle()
    await r.store.set_enabled(r.live.ai_id, False, actor="test", reason="cost")
    r.venue.scale = D("0.9")  # past the 5% stop
    await r.cycle(minutes_later=15)
    assert (await r.portfolio()).positions == ()
    assert {d.reason for d in await r.decisions() if d.action == "sell"} == {"stop_loss"}


async def test_switching_off_mid_cycle_stops_the_remaining_calls_and_the_pending_entry(
    engine: AsyncEngine,
) -> None:
    class Switching(Stub):
        async def analyze(self, request: AIRequest) -> ProviderResult:
            result = await super().analyze(request)
            await r.store.set_enabled(r.live.ai_id, False, actor="test", reason="mid-cycle")
            return result

    stub = Switching(lambda _body: "buy")
    r = await start(engine, stub)
    report = await r.cycle()
    assert report.errors == []
    assert len(stub.requests) == 1  # the first call was already under way; no more were made
    assert (await r.portfolio()).positions == ()  # and its entry was dropped, not sent
    assert "strategy_disabled" in {d.reason for d in await r.decisions()}
