import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data import cli
from ai_trading_lab.market_data.models import Source
from ai_trading_lab.market_data.storage import ObservationStore, RunSummary
from tests.unit.test_market_replay import archive


@pytest.mark.asyncio
async def test_cli_replay_saves_provenance_and_disposes_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = archive()
    path = tmp_path / "sample.json"
    cli.write_archive(path, sample)
    engine = AsyncMock(spec=AsyncEngine)
    save = AsyncMock(return_value=RunSummary(uuid4(), "partial", 2, 2, 0))
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(ObservationStore, "save", save)
    assert await cli.run(cli.parser().parse_args(["replay", str(path)])) == 0
    assert save.await_args is not None
    assert save.await_args.args[0] == Source.KALSHI
    assert save.await_args.args[1].captures[0].data_mode.value == "REPLAY"
    assert json.loads(capsys.readouterr().out)["mode"] == "REPLAY"
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_collect_archive_and_failed_source_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_trading_lab.market_data.replay import ReplayTransport

    sample = archive()

    @asynccontextmanager
    async def transport() -> AsyncIterator[ReplayTransport]:
        yield ReplayTransport(sample)

    engine = AsyncMock(spec=AsyncEngine)
    save = AsyncMock(return_value=RunSummary(uuid4(), "partial", 2, 2, 0))
    monkeypatch.setattr(cli, "HttpTransport", transport)
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(ObservationStore, "save", save)
    path = tmp_path / "new.json"
    arguments = cli.parser().parse_args(
        ["collect", "--source", "kalshi", "--limit", "2", "--capture-file", str(path)]
    )
    assert await cli.run(arguments) == 0
    assert cli.read_archive(path).source == Source.KALSHI
    assert json.loads(capsys.readouterr().out)["mode"] == "REPLAY"
    with pytest.raises(ValueError, match="overwrite"):
        await cli.run(arguments)
    # A failure in one source is persisted and represented by a nonzero exit, not empty-success.
    arguments = cli.parser().parse_args(["collect", "--source", "solana"])
    assert await cli.run(arguments) == 1
    assert "explicit_mints_required" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_bounds_and_persistence_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "sample.json"
    cli.write_archive(path, archive())
    args = cli.parser().parse_args(["collect", "--source", "solana"])
    args.mint = ["x"] * 11
    with pytest.raises(ValueError, match="ten"):
        await cli.run(args)
    engine = AsyncMock(spec=AsyncEngine)
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(
        ObservationStore, "save", AsyncMock(side_effect=ValueError("DB unavailable"))
    )
    with pytest.raises(ValueError, match="DB unavailable"):
        await cli.run(cli.parser().parse_args(["replay", str(path)]))
    engine.dispose.assert_awaited_once()


def test_cli_error_redacts_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["atl-data", "replay", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=ValueError("secret_example")))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "secret_example" not in output
    assert json.loads(output)["status"] == "failed"


def test_cli_success_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["atl-data", "replay", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(return_value=0))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
