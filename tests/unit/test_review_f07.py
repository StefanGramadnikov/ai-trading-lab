"""Worker records rejected source results and handles concurrent operator stop."""

from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.worker import run_worker
from tests.backtest_helpers import research
from tests.unit.test_runtime_source import observed_config


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", [False, True])
async def test_f07_rejected_poll_records_failure_or_returns_concurrent_stop(
    monkeypatch: pytest.MonkeyPatch,
    stopped: bool,
) -> None:
    spec, _ = observed_config()
    initial: dict[str, Any] = {
        "state": {"status": "RUNNING", "poll_attempts": 0, "poll_pending": False}
    }
    pending: dict[str, Any] = {
        "state": {"status": "RUNNING", "poll_attempts": 1, "poll_pending": True}
    }
    terminal: dict[str, Any] = {
        "state": {"status": "STOPPED", "poll_attempts": 1, "poll_pending": False}
    }
    active = AsyncMock()
    active.config.return_value = spec.model_dump(mode="json")
    active.get.side_effect = [initial, terminal if stopped else pending]
    active.append.side_effect = [
        pending,
        ValueError("runtime_session_stopped" if stopped else "runtime_native_event_conflict"),
        terminal,
    ]

    class Stub:
        engine = None

        @asynccontextmanager
        async def worker(self, _: object) -> Any:
            yield active

    monkeypatch.setattr("ai_trading_lab.runtime.worker.queue_checkpoint", AsyncMock())
    collector = AsyncMock(
        return_value={"action": "tick", "frame": research().frames[0].model_dump(mode="json")}
    )
    result = await run_worker(
        cast(Journal, Stub()),
        spec.session_id,
        cycles=1,
        seconds=10,
        collector=collector,
        clock=lambda: spec.started_at_utc,
        monotonic=lambda: 0,
    )
    assert result == terminal
    if stopped:
        assert active.append.await_count == 2
    else:
        assert active.append.await_count == 3
        failure = active.append.await_args_list[-1].args
        assert failure[1] == "poll:1:result"
        assert failure[2] == {"action": "failure", "reason": "rejected_source_result"}
