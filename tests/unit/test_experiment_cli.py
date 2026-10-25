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


@pytest.mark.asyncio
async def test_f13_attach_and_complete_expense_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from ai_trading_lab.experiments.costs import CostAllocation, Expense

    seen: list[tuple[str, Any]] = []

    class Engine:
        async def dispose(self) -> None:
            seen.append(("dispose", None))

    class Store:
        def __init__(self, engine: Any) -> None:
            pass

        async def attach_expense(
            self, eid: Any, key: str, at: Any, expense: Expense
        ) -> dict[str, str]:
            seen.append(("attach", expense.expense_id))
            return {"state": "EVALUATING"}

        async def complete_expenses(
            self, eid: Any, key: str, at: Any, reason: str
        ) -> dict[str, str]:
            seen.append(("complete", reason))
            return {"state": "EVALUATING"}

    monkeypatch.setattr(cli, "create_database_engine", lambda _: Engine())
    monkeypatch.setattr(cli, "ExperimentStore", Store)
    expense = Expense(
        expense_id="invoice-1",
        cost_key="ai:1",
        category="ai",
        kind="actual",
        amount="0.02",
        currency="USD",
        incurred_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        source="operator-supplied-invoice-reference",
        evidence="observed",
        allocations=(CostAllocation(cell_id="hybrid", weight="1"),),
    )
    path = tmp_path / "expense.json"
    path.write_text(expense.model_dump_json())
    eid = str(uuid4())
    stamp = ["--key", "one", "--at", "2026-10-20T00:00:00Z"]
    assert await cli.run(cli.parser().parse_args(["attach-expense", eid, str(path), *stamp])) == 0
    assert (
        await cli.run(
            cli.parser().parse_args(
                ["complete-expenses", eid, *stamp, "--reason", "all bills checked"]
            )
        )
        == 0
    )
    assert [name for name, _ in seen if name != "dispose"] == ["attach", "complete"]
    assert ("attach", "invoice-1") in seen and ("complete", "all bills checked") in seen
    path.write_bytes(b" " * 100_001)
    with pytest.raises(ValueError, match="100KB"):
        cli.read_expense(path)
    capsys.readouterr()
