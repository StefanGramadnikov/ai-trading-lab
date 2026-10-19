import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.paper_execution import cli

FIXTURE = Path(__file__).parents[1] / "fixtures" / "paper_scenario.json"


def sample() -> dict[str, object]:
    result: dict[str, object] = json.loads(FIXTURE.read_text())
    return result


@pytest.mark.parametrize(
    "field,value",
    [
        ("data_mode", "OBSERVED"),
        ("schema_version", True),
        ("initial_cash", "1e19"),
        ("cash_step", "0.0000000000000000001"),
        ("operations", [{"kind": "http", "url": "https://invalid.example"}]),
        ("instructions", "execute this"),
    ],
)
def test_invalid_scenarios(field: str, value: object, tmp_path: Path) -> None:
    data = sample()
    data[field] = value
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        cli.read_scenario(path)


@pytest.mark.parametrize("mutation", ["missing_mode", "quote_mode", "portfolio", "too_many"])
def test_scope_and_operation_bounds(mutation: str, tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_text())
    if mutation == "missing_mode":
        del data["data_mode"]
    elif mutation == "quote_mode":
        data["operations"][0]["quote"]["data_mode"] = "OBSERVED"
    elif mutation == "portfolio":
        data["operations"][0]["intent"]["portfolio_id"] = str(uuid4())
    else:
        data["operations"] = [{"kind": "reconcile"}] * 101
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        cli.read_scenario(path)


def test_input_size_depth_root_and_exact_numbers(tmp_path: Path) -> None:
    path = tmp_path / "scenario.json"
    path.write_bytes(b" " * 1_000_001)
    with pytest.raises(ValueError, match="1MB"):
        cli.read_scenario(path)
    path.write_text("[" * 65 + "0" + "]" * 65)
    with pytest.raises(ValueError, match="depth"):
        cli.read_scenario(path)
    path.write_text("[]")
    with pytest.raises(ValidationError):
        cli.read_scenario(path)
    path.write_text(
        FIXTURE.read_text().replace(
            '"initial_cash": "1000"', '"initial_cash": 1000.000000000000000001'
        )
    )
    assert str(cli.read_scenario(path).initial_cash) == "1000.000000000000000001"
    data = sample()
    data["initial_cash"] = 1.1
    with pytest.raises(ValidationError):
        cli.Scenario.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reconciled,expected",
    [("FILLED", True, 0), ("REJECTED", True, 1), ("EXPIRED", True, 1), ("FILLED", False, 1)],
)
async def test_run_summary_and_cleanup(
    status: str,
    reconciled: bool,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.submit.return_value.result.status = status
    store.submit.return_value.result.reason = "fixture_result"
    store.reconcile.return_value = reconciled
    store.get.return_value.model_dump = lambda **kwargs: {"cash": "989.98"}
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PaperStore", lambda actual: store)
    assert await cli.run(cli.parser().parse_args(["run", str(FIXTURE)])) == expected
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "FIXTURE"
    assert summary["operation_counts"] == {"order": 1, "pause": 0, "settle": 0, "reconcile": 1}
    assert summary["orders"] == [{"status": status, "reason": "fixture_result"}]
    assert summary["reconciled"] is reconciled
    assert store.submit.await_args is not None
    assert store.submit.await_args.args[1].data_mode.value == "FIXTURE"
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["create", "submit", "reconcile", "get"])
async def test_database_failure_always_disposes(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    getattr(store, failure).side_effect = SQLAlchemyError("secret_database_url")
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PaperStore", lambda actual: store)
    with pytest.raises(SQLAlchemyError):
        await cli.run(cli.parser().parse_args(["run", str(FIXTURE)]))
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_process_failure_preserves_prior_operations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = json.loads(FIXTURE.read_text())
    data["operations"] = [
        data["operations"][0],
        {"kind": "pause", "key": "pause-1", "paused": True, "at": "2026-10-18T12:01:00Z"},
    ]
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data))
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.pause.side_effect = ValueError("invalid process transition")
    store.submit.return_value.result.status = "FILLED"
    store.submit.return_value.result.reason = "filled"
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PaperStore", lambda actual: store)
    with pytest.raises(ValueError):
        await cli.run(cli.parser().parse_args(["run", str(path)]))
    store.submit.assert_awaited_once()
    store.pause.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [ValueError("secret_input"), SQLAlchemyError("secret_url"), OSError("secret_path")]
)
def test_main_sanitizes_failures(
    error: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["atl-paper", "run", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=error))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["status"] == "failed"


def test_main_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["atl-paper", "run", "ignored.json"])
    monkeypatch.setattr(cli, "run", AsyncMock(return_value=0))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


@pytest.mark.asyncio
async def test_pause_settlement_and_final_reconciliation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = json.loads(FIXTURE.read_text())
    instrument = data["operations"][0]["intent"]["instrument"]
    instrument.update(kind="prediction", market_id="synthetic-market", outcome="yes")
    data["operations"] = [
        {"kind": "pause", "key": "pause-1", "paused": True, "at": "2026-10-18T12:01:00Z"},
        {
            "kind": "settle",
            "key": "settle-1",
            "instrument": instrument,
            "payout_per_unit": "0",
            "void": False,
            "at": "2026-10-18T12:02:00Z",
        },
    ]
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data))
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.reconcile.return_value = True
    store.get.return_value.model_dump = lambda **kwargs: {"cash": "1000"}
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PaperStore", lambda actual: store)
    assert await cli.run(cli.parser().parse_args(["run", str(path)])) == 0
    store.pause.assert_awaited_once()
    store.settle.assert_awaited_once()
    store.reconcile.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_mode_configuration_rejected_before_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATL_EXECUTION_MODE", "LIVE")
    create = AsyncMock()
    monkeypatch.setattr(cli, "create_database_engine", create)
    with pytest.raises(ValidationError):
        await cli.run(cli.parser().parse_args(["run", str(FIXTURE)]))
    create.assert_not_called()


@pytest.mark.asyncio
async def test_fixture_label_preserves_partial_quote_quality(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = json.loads(FIXTURE.read_text())
    data["operations"][0]["quote"]["quality"] = "PARTIAL"
    data["operations"][0]["quote"]["event_time_utc"] = None
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data))
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.reconcile.return_value = True
    store.get.return_value.model_dump = lambda **kwargs: {"cash": "1000"}
    store.submit.return_value.result.status = "REJECTED"
    store.submit.return_value.result.reason = "quote_quality"
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "PaperStore", lambda actual: store)
    assert await cli.run(cli.parser().parse_args(["run", str(path)])) == 1
    assert store.submit.await_args is not None
    quote = store.submit.await_args.args[1]
    assert quote.quality.value == "PARTIAL"
    assert quote.event_time_utc is None
    engine.dispose.assert_awaited_once()
