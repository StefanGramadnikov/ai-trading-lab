"""The daily review on PostgreSQL: built from a real AI-strategy run, stored once, sent once."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.alerts.monitor import AlertRunner
from ai_trading_lab.alerts.state import AlertState
from ai_trading_lab.config import Settings
from ai_trading_lab.live.store import (
    live_decisions,
    live_equity,
    live_strategies,
    live_strategy_audit,
)
from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.store import NotificationStore
from ai_trading_lab.notifications.telegram import TelegramAdapter
from ai_trading_lab.review import cli, digest
from ai_trading_lab.review.models import Observation
from ai_trading_lab.review.service import REVIEW_AFTER, ReviewService, day_start
from ai_trading_lab.review.store import (
    MAX_DELIVERY_ATTEMPTS,
    ReviewStore,
    daily_reviews,
    review_suggestions,
)
from tests.integration.test_live_ai_engine import AIRig, Stub, start
from tests.integration.test_live_ai_engine import engine as engine  # noqa: F401 (fixture)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
D = Decimal
SETTINGS = Settings(_env_file=None, ai_enabled=True, ai_total_limit_usd=D("10"))


SLOW = Observation(
    code="no_entries",
    severity="watch",
    strategy_id="the-rules-strategy",
    text="Rules breakout has not entered a position in the last 5 days.",
    suggestion="Consider a faster variant as a NEW strategy version.",
)


def canned_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rules are unit-tested; here the storing and the answering are under test."""
    monkeypatch.setattr(digest, "observe", lambda **_: [SLOW])


async def _wipe(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in (
            daily_reviews,
            review_suggestions,
            live_decisions,
            live_equity,
            live_strategy_audit,
        ):
            await connection.execute(delete(table))
        await connection.execute(delete(live_strategies))


@pytest_asyncio.fixture
async def rig(engine: AsyncEngine) -> AsyncIterator[AIRig]:  # noqa: F811
    await _wipe(engine)
    stub = Stub(lambda body: "buy" if body["pair"] != "SOLUSD" else "abstain")
    value = await start(engine, stub, provider="openai", settings=SETTINGS)
    await value.cycle()
    try:
        yield value
    finally:
        await _wipe(engine)


def service(rig: AIRig, now: datetime | None = None) -> ReviewService:
    return ReviewService(rig.engine, SETTINGS, clock=lambda: now or rig.now)


async def test_a_day_of_real_ai_decisions_is_summarised(rig: AIRig) -> None:
    review = await service(rig).build(rig.now.date(), rig.now)
    assert review is not None
    by_kind = {s.kind: s for s in review.strategies}
    assert set(by_kind) == {"rules", "ai", "benchmark"}
    assert by_kind["ai"].buys == 2 and by_kind["ai"].fees > 0  # BTC and ETH bought, SOL abstained
    assert by_kind["benchmark"].buys == 3 and by_kind["rules"].buys == 2
    assert review.ai.active and review.ai.calls == 3 and review.ai.completed == 3
    assert review.ai.spent_usd > 0 and review.ai.abstains == 1
    assert review.ai.total_limit_usd == D("10") and review.ai.total_spent_usd > 0
    assert review.cycles_seen == 1 and review.cycles_expected < 96  # it started that day


async def test_a_day_before_the_loop_existed_has_no_review(rig: AIRig) -> None:
    assert await service(rig).build(rig.now.date() - timedelta(days=30), rig.now) is None


async def test_a_day_without_a_single_cycle_after_the_loop_started_is_still_reported(
    rig: AIRig,
) -> None:
    quiet_day = rig.now.date() + timedelta(days=2)
    review = await service(rig).build(quiet_day, rig.now)
    assert review is not None and review.cycles_seen == 0 and review.cycles_expected == 96
    assert "data_gap" in {o.code for o in review.observations}
    assert all(s.decisions == 0 for s in review.strategies)


async def test_the_day_the_loop_started_expects_only_the_cycles_it_could_have_run(
    rig: AIRig,
) -> None:
    review = await service(rig).build(rig.now.date(), rig.now)
    assert review is not None and review.cycles_expected < 96  # it started during the day


async def test_a_model_that_cannot_be_reached_shows_as_unresolved_calls_at_their_reserved_cost(
    engine: AsyncEngine,  # noqa: F811
) -> None:
    await _wipe(engine)
    try:
        broken = await start(
            engine, Stub(lambda _body: "error"), provider="openai", settings=SETTINGS
        )
        await broken.cycle()
        review = await service(broken).build(broken.now.date(), broken.now)
        assert review is not None
        assert review.ai.calls == 3 and review.ai.unresolved == 3 and review.ai.completed == 0
        assert review.ai.spent_usd > 0 and review.ai.held_usd == 0  # settled at the reservation
        assert "ai_replies_unusable" in {o.code for o in review.observations}
    finally:
        await _wipe(engine)


async def test_the_review_is_created_once_sent_once_and_never_rewritten(rig: AIRig) -> None:
    day = rig.now.date()
    after = day_start(day) + timedelta(days=1) + REVIEW_AFTER + timedelta(minutes=5)
    sent: list[Notification] = []

    async def deliver(event: Notification) -> bool:
        sent.append(event)
        return True

    first = await service(rig, after).run_due(after, deliver)
    assert first == {"day": day.isoformat(), "created": True, "sent": True}
    assert len(sent) == 1 and sent[0].event_key == f"review:{day.isoformat()}:1"
    assert sent[0].kind == "daily_summary" and "Paper only" in sent[0].text
    store = ReviewStore(rig.engine)
    stored = await store.get(day)
    assert stored is not None and stored.delivered_at_utc is not None
    text = stored.text
    again = await service(rig, after + timedelta(hours=1)).run_due(after, deliver)
    assert again["created"] is False and again["sent"] is None and len(sent) == 1
    assert (await store.get(day)).text == text  # type: ignore[union-attr]


async def test_a_day_is_not_reviewed_before_it_has_been_over_for_half_an_hour(rig: AIRig) -> None:
    day = rig.now.date()
    early = day_start(day) + timedelta(days=1) + timedelta(minutes=10)
    report = await service(rig, early).run_due(early, None)
    assert report["created"] is False
    assert not await ReviewStore(rig.engine).has(day)


async def test_a_failed_delivery_is_retried_a_bounded_number_of_times(rig: AIRig) -> None:
    day = rig.now.date()
    after = day_start(day) + timedelta(days=1) + REVIEW_AFTER + timedelta(minutes=5)
    attempts = 0

    async def broken(_: Notification) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    for _ in range(MAX_DELIVERY_ATTEMPTS + 4):
        await service(rig, after).run_due(after, broken)
    assert attempts == MAX_DELIVERY_ATTEMPTS
    stored = await ReviewStore(rig.engine).get(day)
    assert stored is not None and stored.delivered_at_utc is None  # still stored for reading


async def test_without_a_chat_the_review_is_still_stored(rig: AIRig) -> None:
    day = rig.now.date()
    after = day_start(day) + timedelta(days=1) + REVIEW_AFTER + timedelta(minutes=5)
    report = await service(rig, after).run_due(after, None)
    assert report["created"] is True and report["sent"] is None
    assert await ReviewStore(rig.engine).has(day)


async def test_an_older_day_without_a_review_is_kept_but_never_sent_late(rig: AIRig) -> None:
    day = rig.now.date()
    later = day_start(day) + timedelta(days=3) + REVIEW_AFTER + timedelta(minutes=5)
    sent: list[Notification] = []

    async def deliver(event: Notification) -> bool:
        sent.append(event)
        return True

    report = await service(rig, later).run_due(later, deliver)
    newest = day + timedelta(days=2)
    # Only the newest day is sent (a day with no cycle at all is still told); older ones are kept.
    assert report["created"] is True and [e.event_key for e in sent] == [f"review:{newest}:1"]
    assert "0 of 96" in sent[0].text.replace("0/96", "0 of 96")
    store = ReviewStore(rig.engine)
    old = await store.get(day)
    assert old is not None and old.delivered_at_utc is None
    assert old.delivery_attempts == MAX_DELIVERY_ATTEMPTS  # retired, so it is not sent later
    assert (await svc_quiet(rig, later)) is None


async def svc_quiet(rig: AIRig, later: datetime) -> bool | None:
    """A second pass sends nothing more."""
    again = await service(rig, later).run_due(later, None)
    sent: bool | None = again["sent"]
    return sent


async def test_a_slow_rules_strategy_raises_a_suggestion_that_is_asked_once_and_answered(
    rig: AIRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    canned_observation(monkeypatch)
    svc, store = service(rig), ReviewStore(rig.engine)
    review = await svc.create(rig.now.date(), rig.now)
    assert review is not None and "no_entries" in {o.code for o in review.observations}
    pending = await store.suggestions("pending")
    assert [(s.code, s.strategy_id) for s in pending] == [("no_entries", "the-rules-strategy")]
    assert "NEW strategy version" in pending[0].suggestion
    assert await store.pending_count() == 1
    stored = await store.get(rig.now.date())
    assert stored is not None and "1 suggestion(s) wait" in stored.text
    # the same suggestion is not raised again while it waits or after it was approved
    assert not await store.add_suggestion(
        review, "no_entries", "the-rules-strategy", "t", "s", rig.now
    )
    suggestion_id = pending[0].suggestion_id
    assert await store.decide(suggestion_id, "approved", "cli:test", "yes, test it", rig.now)
    assert not await store.decide(suggestion_id, "rejected", "cli:test", "changed mind", rig.now)
    assert not await store.add_suggestion(
        review, "no_entries", "the-rules-strategy", "t", "s", rig.now
    )
    answered = (await store.suggestions("approved"))[0]
    assert (answered.decided_by, answered.decision_reason) == ("cli:test", "yes, test it")
    # a rejected one may come back if the observation still holds
    other = await store.add_suggestion(review, "ai_pace", "", "t", "s", rig.now)
    assert other and await store.decide(
        f"{review.day.isoformat()}:ai_pace:-", "rejected", "cli:test", "fine as is", rig.now
    )
    assert await store.add_suggestion(
        review.model_copy(update={"day": review.day + timedelta(days=1)}),
        "ai_pace",
        "",
        "t",
        "s",
        rig.now,
    )


async def test_the_command_line_builds_shows_and_answers(
    rig: AIRig,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ATL_DATABASE_URL", database_url)
    canned_observation(monkeypatch)
    day = rig.now.date().isoformat()

    async def call(*args: str) -> tuple[int, str]:
        capsys.readouterr()
        code = await cli.run(cli.parser().parse_args(args))
        return code, capsys.readouterr().out

    today = datetime.now(UTC).date().isoformat()
    code, out = await call("run", "--day", today)
    assert code == 1 and json.loads(out)["status"] == "not_over_yet"  # never freeze a partial day
    code, out = await call("run", "--day", day)
    assert code == 0 and json.loads(out)["status"] == "created"
    assert (await call("run", "--day", day))[1].count("exists") == 1
    assert (await call("run", "--day", "2020-01-01"))[0] == 1  # no data that day
    code, out = await call("show", "--day", day)
    assert code == 0 and out.startswith(f"Daily review {day} (UTC). Paper only.")
    code, out = await call("suggestions", "--status", "pending")
    listed = json.loads(out)["suggestions"]
    assert code == 0 and len(listed) == 1 and listed[0]["status"] == "pending"
    code, out = await call("approve", listed[0]["id"], "--reason", "try a faster one")
    assert code == 0 and json.loads(out) == {"id": listed[0]["id"], "status": "approved"}
    code, out = await call("reject", listed[0]["id"], "--reason", "too late")
    assert code == 1 and json.loads(out)["status"] == "not_pending"
    for bad in (["approve", "x"], ["approve", "x", "--reason", " "], ["run", "--day", "nope"]):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(bad)


class Chat:
    """A Telegram stand-in behind the real adapter and the real outbox."""

    def __init__(self) -> None:
        self.down = True
        self.sent: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            return httpx2.Response(500, json={"ok": False})
        self.sent.append(json.loads(request.content)["text"])
        return httpx2.Response(
            200,
            json={"ok": True, "result": {"message_id": len(self.sent), "chat": {"id": -123456}}},
        )


async def test_through_the_real_outbox_a_failed_send_is_tried_again_and_delivered_once(
    rig: AIRig,
) -> None:
    day = rig.now.date()
    after = day_start(day) + timedelta(days=1) + REVIEW_AFTER + timedelta(minutes=5)
    chat = Chat()
    adapter = TelegramAdapter(
        SecretStr("123456:fixture_only_abcdefghijklmnopqrst"),
        SecretStr("-123456"),
        enabled=True,
        transport=httpx2.MockTransport(chat.handle),
    )
    watcher = AlertRunner(AlertState(rig.engine), NotificationStore(rig.engine), adapter)
    svc = service(rig, after)
    first = await svc.run_due(after, watcher.deliver_text)
    assert first["created"] is True and first["sent"] is False  # Telegram was down
    store = ReviewStore(rig.engine)
    stored = await store.get(day)
    assert stored is not None and stored.delivered_at_utc is None and stored.delivery_attempts == 1
    chat.down = False
    second = await svc.run_due(after + timedelta(minutes=5), watcher.deliver_text)
    assert second["sent"] is True and len(chat.sent) == 1
    assert chat.sent[0].endswith(stored.text)
    stored = await store.get(day)
    assert stored is not None and stored.delivered_at_utc is not None
    third = await svc.run_due(after + timedelta(minutes=10), watcher.deliver_text)
    assert third["sent"] is None and len(chat.sent) == 1  # delivered: never again


async def test_a_message_the_outbox_would_refuse_is_retired_not_retried_forever(
    rig: AIRig,
) -> None:
    day = rig.now.date()
    after = day_start(day) + timedelta(days=1) + REVIEW_AFTER + timedelta(minutes=5)
    svc = service(rig, after)
    assert await svc.create(day, after) is not None
    async with rig.engine.begin() as connection:
        await connection.execute(
            update(daily_reviews).values(text="key sk-abcdefghijklmnopqrstuvwxyz0123")
        )
    calls = 0

    async def deliver(_: Notification) -> bool:
        nonlocal calls
        calls += 1
        return True

    report = await svc.run_due(after, deliver)
    assert report["sent"] is False and calls == 0
    stored = await ReviewStore(rig.engine).get(day)
    assert stored is not None and stored.delivery_attempts == MAX_DELIVERY_ATTEMPTS
    assert (await svc.run_due(after, deliver))["sent"] is None  # retired
