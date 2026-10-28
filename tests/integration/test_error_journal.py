"""The error journal on PostgreSQL: grouping, bounded growth, acknowledgement and containment."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.live.journal import (
    KEEP_OCCURRENCES,
    MAX_GROUPS,
    ErrorJournal,
    error_groups,
    error_occurrences,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    async with value.begin() as connection:
        await connection.execute(delete(error_occurrences))
        await connection.execute(delete(error_groups))
    try:
        yield value
    finally:
        async with value.begin() as connection:
            await connection.execute(delete(error_occurrences))
            await connection.execute(delete(error_groups))
        await value.dispose()


def failure(text: str = "bad value") -> ValueError:
    try:
        raise ValueError(text)
    except ValueError as exc:
        return exc


async def test_an_exception_is_stored_once_per_problem_with_counts_and_a_redacted_trace(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    for minute in range(3):
        ok = await journal.record_exception(
            "ai_strategy",
            failure("api_key=SuperSecretValue123 broke"),
            strategy_id="v0-ai",
            pair="XBTEUR",
            context={"cycle": minute},
            at=NOW + timedelta(minutes=minute),
        )
        assert ok is True
    (group,) = await journal.groups()
    assert (group.component, group.error_type, group.status, group.count) == (
        "ai_strategy",
        "ValueError",
        "new",
        3,
    )
    assert group.first_seen_utc == NOW and group.last_seen_utc == NOW + timedelta(minutes=2)
    assert (group.last_strategy_id, group.last_pair, group.last_context) == (
        "v0-ai",
        "XBTEUR",
        {"cycle": 2},
    )
    assert "SuperSecretValue123" not in (group.last_message or "")
    assert "SuperSecretValue123" not in (group.last_traceback or "")
    assert "ValueError" in (group.last_traceback or "")
    assert len(await journal.occurrences(group.fingerprint)) == 3


async def test_a_problem_that_repeats_every_cycle_cannot_grow_the_database(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    for n in range(KEEP_OCCURRENCES * 3):
        await journal.record_event(
            "data_collection", "network_error", f"try {n}", at=NOW + timedelta(seconds=n)
        )
    async with engine.connect() as connection:
        groups = await connection.scalar(select(func.count()).select_from(error_groups))
        rows = await connection.scalar(select(func.count()).select_from(error_occurrences))
    assert groups == 1 and rows == KEEP_OCCURRENCES
    (group,) = await journal.groups()
    assert group.count == KEEP_OCCURRENCES * 3  # the count still tells the whole story
    newest = await journal.occurrences(group.fingerprint, limit=1)
    assert newest[0].message == f"try {KEEP_OCCURRENCES * 3 - 1}"


async def test_warnings_need_no_acknowledgement_and_errors_reopen_when_they_recur(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    await journal.record_event("data_collection", "network_error", "kraken", at=NOW)
    exc = failure()
    await journal.record_exception("rules_strategy", exc, at=NOW)
    summary = await journal.summary(NOW)
    assert (summary.unacknowledged_errors, summary.errors_24h, summary.warnings_24h) == (1, 1, 1)
    (warning,) = await journal.groups(level="warning")
    assert warning.status == "seen"
    assert await journal.acknowledge(None, actor="cli:me", reason="known", at=NOW) == 1
    assert (await journal.summary(NOW)).unacknowledged_errors == 0
    (done,) = await journal.groups(status="acknowledged")
    assert (done.acknowledged_by, done.acknowledged_reason) == ("cli:me", "known")
    # The same problem again: it needs the owner's eyes again; a repeated warning stays quiet.
    await journal.record_exception("rules_strategy", exc, at=NOW + timedelta(hours=1))
    await journal.record_event(
        "data_collection", "network_error", "kraken", at=NOW + timedelta(hours=1)
    )
    assert (await journal.summary(NOW + timedelta(hours=1))).unacknowledged_errors == 1
    assert (await journal.groups(level="warning"))[0].status == "seen"


async def test_acknowledge_by_prefix_and_unknown_prefix(engine: AsyncEngine) -> None:
    journal = ErrorJournal(engine)
    await journal.record_exception("worker", failure("one"), at=NOW)
    (group,) = await journal.groups()
    assert await journal.acknowledge("zzzz", actor="a", reason="r", at=NOW) == 0
    assert (await journal.group(group.fingerprint[:8])) is not None
    assert await journal.acknowledge(group.fingerprint[:8], actor="a", reason="r", at=NOW) == 1
    assert await journal.group("zzzz") is None


async def test_old_handled_problems_expire_but_an_unacknowledged_error_never_does(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    await journal.record_exception("worker", failure("kept"), at=NOW - timedelta(days=90))
    await journal.record_event("data_collection", "old_warning", at=NOW - timedelta(days=90))
    # The next write after an hour runs the prune.
    await journal.record_event("data_collection", "fresh", at=NOW)
    kinds = {g.error_type for g in await journal.groups()}
    assert kinds == {"ValueError", "fresh"}


async def test_the_number_of_problems_is_capped(engine: AsyncEngine) -> None:
    journal = ErrorJournal(engine)
    for n in range(MAX_GROUPS + 25):
        await journal.record_event(
            "data_collection", f"kind-{n}", at=NOW - timedelta(days=1) + timedelta(seconds=n)
        )
    await journal.record_event("data_collection", "trigger", at=NOW)
    async with engine.connect() as connection:
        total = await connection.scalar(select(func.count()).select_from(error_groups))
        orphans = await connection.scalar(
            select(func.count())
            .select_from(error_occurrences)
            .where(error_occurrences.c.fingerprint.not_in(select(error_groups.c.fingerprint)))
        )
    assert total is not None and total <= MAX_GROUPS
    assert orphans == 0


async def test_a_broken_database_never_raises_into_the_loop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = MagicMock()
    broken.begin = MagicMock(side_effect=ConnectionError("db gone password=abcdef123456"))
    journal = ErrorJournal(broken)
    assert await journal.record_exception("worker", failure(), at=NOW) is False
    assert await journal.record_event("worker", "x", at=NOW) is False
    err = capsys.readouterr().err
    assert "error journal failed" in err and "abcdef123456" not in err


async def test_context_is_redacted_and_bounded_and_text_fields_are_clipped(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    loop: dict[str, object] = {}
    loop["self"] = loop
    assert await journal.record_event(
        "data_collection",
        "kinds",
        "x",
        context={"url": "https://h/x?api_key=SuperSecretValue123", "n": 1},
        pair="P" * 40,
        strategy_id="s" * 300,
        at=NOW,
    )
    assert await journal.record_event("data_collection", "huge", context={"a": "x" * 5000}, at=NOW)
    assert await journal.record_event("data_collection", "loop", context=loop, at=NOW)
    assert await journal.record_event(
        "c" * 80, "t" * 300, "m", at=NOW
    )  # over-long names are clipped
    by_type = {g.error_type: g for g in await journal.groups(limit=10)}
    kinds = by_type["kinds"]
    assert "SuperSecretValue123" not in str(kinds.last_context) and kinds.last_context["n"] == 1  # type: ignore[index]
    assert len(kinds.last_pair or "") == 12 and len(kinds.last_strategy_id or "") == 100
    assert by_type["huge"].last_context == {"truncated": True}
    assert by_type["loop"].last_context == {"unserializable": True}
    assert any(len(g.component) == 30 and len(g.error_type) <= 100 for g in by_type.values())


async def test_nul_bytes_and_a_gigantic_message_are_stored_safely_and_fast(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    assert await journal.record_exception(
        "worker", ValueError("bad\x00byte " + "word " * 1_000_000), at=NOW
    )
    (group,) = await journal.groups()
    assert "\x00" not in (group.last_message or "") and len(group.last_message or "") <= 500
    assert len(group.last_traceback or "") <= 6000


async def test_prefix_matching_is_literal_and_an_ambiguous_prefix_is_refused(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    await journal.record_exception("worker", failure("one"), at=NOW)
    await journal.record_event("data_collection", "other", level="error", at=NOW)
    assert await journal.acknowledge("%", actor="a", reason="r", at=NOW) == 0
    assert await journal.acknowledge("_", actor="a", reason="r", at=NOW) == 0
    assert await journal.group("%") is None
    with pytest.raises(ValueError, match="ambiguous"):
        await journal.acknowledge("", actor="a", reason="r", at=NOW)
    assert (await journal.summary(NOW)).unacknowledged_errors == 2  # nothing was acknowledged


async def test_an_error_and_a_warning_with_the_same_place_are_separate_problems(
    engine: AsyncEngine,
) -> None:
    journal = ErrorJournal(engine)
    await journal.record_event("data_collection", "same", "e", level="error", at=NOW)
    await journal.record_event("data_collection", "same", "w", level="warning", at=NOW)
    groups = await journal.groups()
    assert {(g.level, g.status) for g in groups} == {("error", "new"), ("warning", "seen")}


async def test_even_unacknowledged_errors_have_a_hard_cap(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_trading_lab.live.journal as module

    monkeypatch.setattr(module, "MAX_GROUPS", 10)
    monkeypatch.setattr(module, "HARD_MAX_GROUPS", 20)
    journal = ErrorJournal(engine)
    for n in range(30):
        await journal.record_event(
            "data_collection", f"e{n}", level="error", at=NOW - timedelta(hours=2, seconds=-n)
        )
    await journal.record_event("data_collection", "trigger", level="error", at=NOW)
    kept = {g.error_type for g in await journal.groups(limit=100)}
    assert len(kept) <= 20 and "trigger" in kept and "e0" not in kept


async def test_after_a_failure_the_journal_stands_down_instead_of_stalling_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = MagicMock()
    broken.begin = MagicMock(side_effect=ConnectionError("down"))
    journal = ErrorJournal(broken)
    assert await journal.record_event("worker", "one", at=NOW) is False
    assert await journal.record_event("worker", "two", at=NOW) is False
    assert await journal.record_exception("worker", failure(), at=NOW) is False
    assert broken.begin.call_count == 1  # the later calls did not even try


async def test_a_write_that_hangs_is_abandoned_after_the_timeout(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import ai_trading_lab.live.journal as module

    async def hang(self: ErrorJournal, **_: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(module, "WRITE_TIMEOUT", 0.05)
    monkeypatch.setattr(ErrorJournal, "_write", hang)
    assert await ErrorJournal(engine).record_event("worker", "slow", at=NOW) is False
