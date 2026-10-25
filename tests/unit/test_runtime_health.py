"""Read-only worker progress probe; healthy does not assert process or feed liveness."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from ai_trading_lab.runtime import health

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def row(*, age: int = 0, status: str = "RUNNING") -> dict[str, Any]:
    return {
        "config": {
            "poll_interval_seconds": 10,
            "deadline_at_utc": (NOW + timedelta(hours=1)).isoformat(),
        },
        "checkpoint": {
            "last_at": (NOW - timedelta(seconds=age)).isoformat(),
            "state": {"status": status},
        },
    }


@pytest.mark.parametrize(
    "age,expected", [(0, "current"), (50, "current"), (51, "stale"), (-1, "invalid_progress")]
)
def test_progress_cadence_boundary_and_clock_regression(age: int, expected: str) -> None:
    assert health.evaluate_progress(row(age=age), NOW)["reason"] == expected


@pytest.mark.parametrize("status", ["PAUSED", "DEGRADED"])
def test_current_suspensions_are_reported_without_clearing(status: str) -> None:
    saved = row(status=status)
    assert health.evaluate_progress(saved, NOW) == {
        "healthy": True,
        "reason": "current",
        "runtime_status": status,
    }
    assert saved == row(status=status)


@pytest.mark.parametrize(
    "case", ["missing", "stopped", "expired", "naive", "bad_interval", "bad_status", "bad_state"]
)
def test_terminal_missing_and_invalid_progress_fail_closed(case: str) -> None:
    saved = row()
    if case == "missing":
        assert health.evaluate_progress(None, NOW)["healthy"] is False
        return
    if case == "stopped":
        saved["checkpoint"]["state"]["status"] = "STOPPED"
    elif case == "expired":
        saved["config"]["deadline_at_utc"] = NOW.isoformat()
    elif case == "naive":
        saved["checkpoint"]["last_at"] = NOW.replace(tzinfo=None).isoformat()
    elif case == "bad_interval":
        saved["config"]["poll_interval_seconds"] = True
    elif case == "bad_status":
        saved["checkpoint"]["state"]["status"] = "BOGUS"
    elif case == "bad_state":
        saved["checkpoint"]["state"] = []
    assert health.evaluate_progress(saved, NOW)["healthy"] is False


@pytest.mark.asyncio
async def test_probe_reads_one_row_in_readonly_transaction_without_locks() -> None:
    engine, connection = MagicMock(), AsyncMock()
    engine.begin.return_value.__aenter__.return_value = connection
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = row()
    connection.execute.side_effect = [MagicMock(), result]
    assert (await health.probe(engine, UUID(int=1), now=NOW))["healthy"] is True
    sql = [str(call.args[0]) for call in connection.execute.await_args_list]
    assert sql[0] == "SET TRANSACTION READ ONLY"
    assert len(sql) == 2
    assert "runtime_sessions" in sql[1]
    assert "runtime_events" not in sql[1]
    assert "FOR UPDATE" not in sql[1]
    assert "advisory" not in sql[1]


@pytest.mark.asyncio
async def test_probe_timeout_is_bounded_and_sanitized() -> None:
    engine = MagicMock()
    engine.begin.side_effect = TimeoutError("private-db-url")
    assert await health.probe(engine, UUID(int=1), now=NOW) == {
        "healthy": False,
        "reason": "persistence_unavailable",
    }


@pytest.mark.parametrize("healthy,code", [(True, 0), (False, 1)])
def test_module_exit_code_disposes_engine(healthy: bool, code: int, capsys: Any) -> None:
    engine = AsyncMock()
    with (
        patch.object(health, "create_database_engine", return_value=engine),
        patch.object(health, "Settings"),
        patch.object(health, "probe", AsyncMock(return_value={"healthy": healthy})),
        patch("sys.argv", ["health", str(UUID(int=1))]),
    ):
        with pytest.raises(SystemExit) as caught:
            health.main()
    assert caught.value.code == code
    engine.dispose.assert_awaited_once()
    assert "healthy" in capsys.readouterr().out


def test_compose_bounded_restart_and_worker_progress_healthcheck() -> None:
    from pathlib import Path

    worker = Path("compose.yaml").read_text().split("  worker:", 1)[1]
    assert 'restart: "on-failure:3"' in worker
    assert "ai_trading_lab.runtime.health" in worker
    assert "ATL_RUNTIME_SESSION_ID" in worker
    assert "healthcheck:" in worker
