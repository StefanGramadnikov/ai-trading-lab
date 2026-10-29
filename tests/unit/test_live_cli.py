"""atl-live command wiring; the work itself is covered by the engine and worker tests."""

import argparse
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_trading_lab.config import Settings
from ai_trading_lab.live import cli
from ai_trading_lab.live.worker import AlreadyRunning, LoopResult


def args(*argv: str) -> argparse.Namespace:
    return cli.parser().parse_args(list(argv))


def test_parser_requires_an_explicit_bound_or_an_explicit_unbounded_run() -> None:
    assert args("run", "--cycles", "3").cycles == 3
    assert args("run", "--until-stopped").until_stopped is True
    with pytest.raises(SystemExit):
        args("run")
    with pytest.raises(SystemExit):
        args("run", "--cycles", "3", "--until-stopped")
    with pytest.raises(SystemExit):
        args("run", "--cycles", "0")
    assert args("setup").command == "setup" and args("status").command == "status"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["setup", "cycle", "status"])
async def test_commands_dispatch_and_always_dispose_the_engine(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    engine, live = AsyncMock(), AsyncMock()
    live.run_cycle.return_value = MagicMock(model_dump=lambda mode: {"cycle": "ok"}, errors=[])

    class Held:
        async def __aenter__(self) -> Any:
            return AsyncMock()

        async def __aexit__(self, *_: Any) -> None:
            return None

    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "single_instance", return_value=Held()),
        patch.object(cli, "summary", AsyncMock(return_value={"status": "ok"})),
    ):
        assert await cli.run(args(command)) == 0
    live.setup.assert_awaited_once() if command in ("setup", "cycle") else None
    engine.dispose.assert_awaited_once()
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_run_holds_the_single_instance_lock_and_reports_a_second_instance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine, live = AsyncMock(), AsyncMock()

    class Busy:
        async def __aenter__(self) -> None:
            raise AlreadyRunning("another live loop is already running")

        async def __aexit__(self, *_: Any) -> None:
            return None

    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "single_instance", return_value=Busy()),
    ):
        with pytest.raises(AlreadyRunning):
            await cli.run(args("run", "--cycles", "1"))
    engine.dispose.assert_awaited_once()
    live.run_cycle.assert_not_awaited()


def test_main_sanitizes_failures(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(cli, "parser") as parser,
        patch.object(cli, "run", AsyncMock(side_effect=ValueError("secret-url"))),
    ):
        parser.return_value.parse_args.return_value = argparse.Namespace()
        with pytest.raises(SystemExit) as error:
            cli.main()
    assert error.value.code == 2
    assert "secret-url" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "expected"), [(["--cycles", "2"], 2), (["--until-stopped"], None)]
)
async def test_run_passes_the_bound_the_interval_and_the_lock_to_the_loop(
    flag: list[str], expected: int | None
) -> None:
    engine, live = AsyncMock(), AsyncMock()
    live.clock = lambda: None
    entered: list[str] = []

    class Held:
        async def __aenter__(self) -> Any:
            entered.append("lock")
            return AsyncMock()

        async def __aexit__(self, *_: Any) -> None:
            entered.append("released")

    loop = AsyncMock(return_value=LoopResult(done=2, failed=0))
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "single_instance", return_value=Held()),
        patch.object(cli, "run_loop", loop),
    ):
        assert await cli.run(args("run", *flag)) == 0
    assert loop.await_args is not None
    kwargs = loop.await_args.kwargs
    assert kwargs["cycles"] == expected and kwargs["interval"] == 900 and kwargs["offset"] == 20
    assert entered == ["lock", "released"]
    live.setup.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bounded_run_in_which_cycles_failed_does_not_exit_zero() -> None:
    engine, live = AsyncMock(), AsyncMock()
    live.clock = lambda: None

    class Held:
        async def __aenter__(self) -> Any:
            return AsyncMock()

        async def __aexit__(self, *_: Any) -> None:
            return None

    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "single_instance", return_value=Held()),
        patch.object(cli, "run_loop", AsyncMock(return_value=LoopResult(done=3, failed=3))),
    ):
        assert await cli.run(args("run", "--cycles", "3")) == 1


@pytest.mark.asyncio
async def test_a_manual_cycle_takes_the_same_lock_as_the_loop() -> None:
    engine, live = AsyncMock(), AsyncMock()
    live.run_cycle.return_value = MagicMock(model_dump=lambda mode: {"cycle": "ok"}, errors=[])

    class Busy:
        async def __aenter__(self) -> None:
            raise AlreadyRunning("busy")

        async def __aexit__(self, *_: Any) -> None:
            return None

    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "single_instance", return_value=Busy()),
    ):
        with pytest.raises(AlreadyRunning):
            await cli.run(args("cycle"))
    live.run_cycle.assert_not_awaited()


def ai_settings(**changes: Any) -> Settings:
    from ai_trading_lab.ai.models import PriceSnapshot

    price = PriceSnapshot(
        provider="openai",
        model="gpt-6.1-sol",
        input_per_million="2",
        output_per_million="10",
        source_url="https://example.test/pricing",
        verified_at_utc="2026-10-25T00:00:00Z",
        version="test",
    )
    values: dict[str, Any] = {
        "ai_enabled": True,
        "openai_api_key": "synthetic-not-a-key",
        "ai_total_limit_usd": "10",
        "ai_reviewed_prices": (price,),
    }
    return Settings(_env_file=None, **(values | changes))


@pytest.mark.parametrize(
    ("changes", "note"),
    [
        ({"ai_enabled": False}, "ai_disabled"),
        ({"openai_api_key": None}, "ai_key_missing"),
        ({"ai_total_limit_usd": "0"}, "ai_spending_cap_is_zero"),
        ({"ai_reviewed_prices": ()}, "ai_price_not_reviewed"),
        ({"ai_model": "gpt-9-unreviewed"}, "ai_model_unsupported"),
    ],
)
def test_the_ai_strategy_stays_off_unless_fully_and_explicitly_configured(
    changes: dict[str, Any], note: str
) -> None:
    deps, why = cli.build_ai(ai_settings(**changes), MagicMock())
    assert deps is None and why == note


def test_a_fully_configured_ai_strategy_is_built_with_the_reviewed_price() -> None:
    deps, why = cli.build_ai(ai_settings(), MagicMock())
    assert why == "ai_enabled" and deps is not None
    assert deps.price.model == deps.config.model == "gpt-6.1-sol"
    assert deps.config.provider == "openai" and deps.config.total_limit_usd == 15


def test_the_model_is_chosen_by_setting_and_needs_its_own_reviewed_price() -> None:
    # The price on file is for gpt-6.1-sol; asking for the other supported model finds no price.
    deps, why = cli.build_ai(ai_settings(ai_model="gpt-5.6-terra"), MagicMock())
    assert deps is None and why == "ai_price_not_reviewed"
    assert Settings(_env_file=None).ai_model == "gpt-6.1-sol"


def test_the_reasoning_allowance_covers_a_reasoning_model() -> None:
    # Reasoning tokens are billed and counted inside max_output_tokens: 400 would cut every answer.
    from ai_trading_lab.live.ai_strategy import AIConfig

    assert AIConfig().max_output_tokens >= 2000


def test_toggle_commands_need_a_strategy_and_a_reason() -> None:
    assert args("disable", "ai", "--reason", " too costly ").reason == "too costly"
    assert args("enable", "rules", "--reason", "back").strategy == "rules"
    for bad in (["disable", "ai"], ["disable", "--reason", "x"], ["enable", "ai", "--reason", " "]):
        with pytest.raises(SystemExit):
            args(*bad)
    with pytest.raises(SystemExit):
        args("enable", "ai", "--reason", "x" * 201)
    assert args("audit").limit == 20 and args("audit", "--limit", "3").limit == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argv", "target", "enabled"),
    [
        (["disable", "ai", "--reason", "cost"], "ai-id", False),
        (["enable", "rules", "--reason", "back"], "rules-id", True),
        (["disable", "benchmark", "--reason", "x"], "bench-id", False),
        (["disable", "v0-custom", "--reason", "x"], "v0-custom", False),
    ],
)
async def test_toggle_commands_resolve_aliases_and_record_the_actor(
    argv: list[str], target: str, enabled: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    engine, live = AsyncMock(), MagicMock()
    live.rules_id, live.ai_id, live.benchmark_id = "rules-id", "ai-id", "bench-id"
    live.store.set_enabled = AsyncMock(return_value=True)
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch("getpass.getuser", return_value="stefan"),
    ):
        assert await cli.run(args(*argv)) == 0
    live.store.set_enabled.assert_awaited_once_with(
        target, enabled, actor="cli:stefan", reason=argv[-1]
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"strategy_id": target, "enabled": enabled, "changed": True}
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_command_prints_the_trail(capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    from ai_trading_lab.live.store import AuditRow

    row = AuditRow(
        seq=1,
        strategy_id="s",
        at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        actor="cli:x",
        action="disable",
        enabled=False,
        reason="r",
    )
    engine, live = AsyncMock(), MagicMock()
    live.store.audit = AsyncMock(return_value=[row])
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
    ):
        assert await cli.run(args("audit", "--limit", "5")) == 0
    live.store.audit.assert_awaited_once_with(limit=5)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["audit"][0]["reason"] == "r" and out["audit"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_an_unknown_strategy_is_reported_as_such(capsys: pytest.CaptureFixture[str]) -> None:
    engine, live = AsyncMock(), MagicMock()
    live.rules_id, live.ai_id, live.benchmark_id = "r", "a", "b"
    live.store.set_enabled = AsyncMock(side_effect=ValueError("unknown strategy"))
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
    ):
        assert await cli.run(args("disable", "typo", "--reason", "x")) == 2
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"status": "failed", "error": "unknown strategy", "strategy_id": "typo"}
    engine.dispose.assert_awaited_once()


def test_the_actor_falls_back_to_the_uid_without_a_passwd_entry() -> None:
    with patch("getpass.getuser", side_effect=KeyError("no entry")):
        assert cli._user().startswith("uid:")


@pytest.mark.asyncio
@pytest.mark.parametrize(("healthy", "code"), [(True, 0), (False, 1)])
async def test_health_exits_zero_only_when_the_loop_is_alive(
    healthy: bool, code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    engine, live = AsyncMock(), MagicMock()
    live.clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
    verdict: dict[str, Any] = {
        "healthy": healthy,
        "reason": "ok" if healthy else "stale",
        "age_seconds": {},
    }
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "health", AsyncMock(return_value=verdict)) as probe,
    ):
        assert await cli.run(args("health", "--max-age-minutes", "25")) == code
    assert probe.await_args is not None
    assert probe.await_args.args[2].total_seconds() == 25 * 60
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["healthy"] is healthy
    engine.dispose.assert_awaited_once()
    assert args("health").max_age_minutes == 40


def _journal_group(**changes: Any) -> Any:
    from datetime import UTC, datetime

    from ai_trading_lab.live.journal import ErrorGroup

    values: dict[str, Any] = {
        "fingerprint": "ab12" * 16,
        "level": "error",
        "component": "ai_strategy",
        "error_type": "ValueError",
        "location": "live.ai_strategy:propose",
        "headline": "ValueError in live.ai_strategy:propose",
        "status": "new",
        "count": 2,
        "first_seen_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "last_seen_utc": datetime(2026, 1, 2, tzinfo=UTC),
        "last_message": "bad",
        "last_traceback": "Traceback...",
    }
    return ErrorGroup(**(values | changes))


async def _run_with_journal(argv: list[str], journal: Any) -> int:
    engine, live = AsyncMock(), MagicMock()
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "LiveEngine", return_value=live),
        patch.object(cli, "ErrorJournal", return_value=journal),
        patch("getpass.getuser", return_value="stefan"),
    ):
        return await cli.run(args(*argv))


@pytest.mark.asyncio
async def test_errors_lists_problems_without_the_heavy_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_trading_lab.live.journal import JournalSummary

    journal = MagicMock()
    journal.groups = AsyncMock(return_value=[_journal_group()])
    journal.summary = AsyncMock(
        return_value=JournalSummary(unacknowledged_errors=1, errors_24h=2, warnings_24h=0)
    )
    assert await _run_with_journal(["errors", "--status", "new", "--limit", "5"], journal) == 0
    journal.groups.assert_awaited_once_with(status="new", level=None, limit=5)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["summary"]["unacknowledged_errors"] == 1
    (problem,) = out["problems"]
    assert problem["fingerprint"] == "ab12ab12ab12" and "last_traceback" not in problem
    assert problem["count"] == 2 and problem["status"] == "new"


@pytest.mark.asyncio
async def test_errors_show_prints_the_full_problem_and_unknown_prefixes_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal = MagicMock()
    journal.group = AsyncMock(return_value=_journal_group())
    journal.occurrences = AsyncMock(return_value=[])
    assert await _run_with_journal(["errors", "--show", "ab12"], journal) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["problem"]["last_traceback"] == "Traceback..." and out["recent_occurrences"] == []
    journal.group = AsyncMock(return_value=None)
    assert await _run_with_journal(["errors", "--show", "ffff"], journal) == 2
    assert "no single problem" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "expected"), [("all", None), ("ab12", "ab12")])
async def test_ack_records_who_and_why(
    target: str, expected: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = MagicMock()
    journal.acknowledge = AsyncMock(return_value=2)
    assert await _run_with_journal(["ack", target, "--reason", "known issue"], journal) == 0
    journal.acknowledge.assert_awaited_once_with(expected, actor="cli:stefan", reason="known issue")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"acknowledged": 2}
    with pytest.raises(SystemExit):
        args("ack", "all")  # a reason is required


def test_fingerprint_arguments_must_be_hex_prefixes() -> None:
    assert args("errors", "--show", "ab12").show == "ab12"
    assert args("ack", "all", "--reason", "x").fingerprint == "all"
    for bad in (
        ["errors", "--show", "%"],
        ["errors", "--show", "ab"],
        ["ack", "_", "--reason", "x"],
    ):
        with pytest.raises(SystemExit):
            args(*bad)


@pytest.mark.asyncio
async def test_an_ambiguous_ack_prefix_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    journal = MagicMock()
    journal.acknowledge = AsyncMock(side_effect=ValueError("ambiguous fingerprint prefix"))
    assert await _run_with_journal(["ack", "ab12", "--reason", "x"], journal) == 2
    assert "more than one problem" in capsys.readouterr().out
