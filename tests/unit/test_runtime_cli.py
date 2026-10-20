import argparse
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from ai_trading_lab.config import Settings
from ai_trading_lab.runtime import cli
from tests.unit.test_runtime_engine import config


def test_input_bound_parser_and_json_errors(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"x" * 2_000_001)
    with pytest.raises(ValueError, match="2MB"):
        cli.read_json(path)
    path.write_text('{"bad":NaN}')
    with pytest.raises(ValueError):
        cli.read_json(path)
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["worker", str(UUID(int=1)), "--cycles", "1", "--seconds", "1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["create", "show", "step", "pause", "resume", "stop", "worker"])
async def test_commands_and_cleanup(command: str, tmp_path: Path, capsys: Any) -> None:
    import json

    raw = config()
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(raw))
    if command == "step":
        path.write_text('{"action":"tick","frame":{}}')
    engine, journal = AsyncMock(), AsyncMock()
    journal.create.return_value = journal.get.return_value = journal.append.return_value = {
        "state": {"status": "RUNNING"}
    }
    args = argparse.Namespace(
        command=command,
        configuration=path,
        event=path,
        session_id=UUID(str(raw["session_id"])),
        key="key",
        at=datetime.fromisoformat(raw["started_at_utc"]),
        cycles=1,
        seconds=1,
    )
    with (
        patch.object(cli, "queue_checkpoint", AsyncMock()),
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "Journal", return_value=journal),
        patch.object(cli, "run_worker", AsyncMock(return_value={"state": {"status": "RUNNING"}})),
    ):
        assert await cli.run(args) == 0
    assert "RUNNING" in capsys.readouterr().out
    engine.dispose.assert_awaited_once()


def test_main_sanitized(capsys: Any) -> None:
    with (
        patch.object(cli, "parser") as parser,
        patch.object(cli, "run", AsyncMock(side_effect=ValueError("private-url"))),
    ):
        parser.return_value.parse_args.return_value = argparse.Namespace()
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    assert "private-url" not in capsys.readouterr().out
