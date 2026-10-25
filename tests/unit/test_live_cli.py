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
