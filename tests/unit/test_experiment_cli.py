"""CLI never activates HTTP inference or exposes local secrets."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ai_trading_lab.config import Settings
from ai_trading_lab.experiments import cli
from tests.experiment_helpers import experiment


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None))


def test_bounded_exact_config_and_parser(tmp_path: Path) -> None:
    spec = experiment()
    path = tmp_path / "run.json"
    path.write_text(spec.model_dump_json())
    assert cli.read_config(path) == spec
    parsed = cli.parser().parse_args(
        ["create", str(path), "--key", "new", "--at", "2026-10-20T00:00:00Z"]
    )
    assert parsed.at.tzinfo is not None
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="2MB"):
        cli.read_config(path)
    path.write_text('{"schema_version": "1", "schema_version":"2"}')
    with pytest.raises(ValueError):
        cli.read_config(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "run", "report", "pause"])
async def test_dispatch_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    calls: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            calls.append("dispose")

    class Store:
        def __init__(self, engine: Any) -> None:
            pass

        async def create(self, *args: Any) -> dict[str, str]:
            calls.append("create")
            return {"state": "CREATED"}

        async def advance(self, *args: Any) -> dict[str, str]:
            calls.append("run")
            return {"state": "RUNNING"}

        async def report(self, *args: Any) -> dict[str, str]:
            calls.append("report")
            return {"state": "EVALUATING"}

        async def command(self, *args: Any) -> dict[str, str]:
            calls.append("pause")
            return {"state": "PAUSED"}

    monkeypatch.setattr(cli, "create_database_engine", lambda _: Engine())
    monkeypatch.setattr(cli, "ExperimentStore", Store)
    path = tmp_path / "spec.json"
    path.write_text(experiment().model_dump_json())
    args = [action, str(path) if action == "create" else str(uuid4())]
    if action != "report":
        args += ["--key", "one", "--at", "2026-10-20T00:00:00Z"]
    if action == "pause":
        args += ["--reason", "manual"]
    assert await cli.run(cli.parser().parse_args(args)) == 0
    assert calls == [action, "dispose"]
    assert "state" in json.loads(capsys.readouterr().out)


def test_invalid_input_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atl-experiment",
            "create",
            "/does/not/exist",
            "--key",
            "one",
            "--at",
            "2026-10-20T00:00:00Z",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
