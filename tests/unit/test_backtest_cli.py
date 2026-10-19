import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.backtesting import cli
from tests.unit.test_backtest_reporting import spec_sample


def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-research.json"
    path.write_text(spec_sample().model_dump_json())
    return path


def test_read_bounds_schema_and_exact_decimals(tmp_path: Path) -> None:
    path = input_file(tmp_path)
    expected = spec_sample()
    assert cli.read_research(path) == expected
    path.write_text(
        path.read_text().replace('"initial_cash":"1000"', '"initial_cash":1000.000000000000000001')
    )
    assert str(cli.read_research(path).initial_cash) == "1000.000000000000000001"
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="2MB"):
        cli.read_research(path)
    for invalid in ("[]", '{"data_mode":"OBSERVED"}', "[" * 65 + "0" + "]" * 65):
        path.write_text(invalid)
        with pytest.raises(ValueError):
            cli.read_research(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_run_disposes_engine_on_success_and_failure(
    failure: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = input_file(tmp_path)
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.run.return_value = {"counts": {"rejections": 1}, "nav": None}
    if failure:
        store.run.side_effect = SQLAlchemyError("secret_database_url")
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "ResearchStore", lambda actual: store)
    arguments = cli.parser().parse_args(["run", str(path)])
    if failure:
        with pytest.raises(SQLAlchemyError):
            await cli.run(arguments)
    else:
        assert await cli.run(arguments) == 0
        assert json.loads(capsys.readouterr().out) == store.run.return_value
    store.run.assert_awaited_once_with(spec_sample())
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [ValueError("secret_payload"), SQLAlchemyError("secret_url"), OSError("secret_path")]
)
def test_main_sanitizes_errors(
    error: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["atl-backtest", "run", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=error))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["status"] == "failed"


def test_main_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["atl-backtest", "run", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(return_value=0))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


@pytest.mark.asyncio
async def test_live_mode_refused_before_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATL_EXECUTION_MODE", "LIVE")
    engine = AsyncMock()
    monkeypatch.setattr(cli, "create_database_engine", engine)
    with pytest.raises(ValidationError):
        await cli.run(cli.parser().parse_args(["run", str(input_file(tmp_path))]))
    engine.assert_not_called()
