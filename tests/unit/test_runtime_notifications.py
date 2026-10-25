from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from ai_trading_lab.runtime.notifications import queue_checkpoint


@pytest.mark.asyncio
async def test_healthy_checkpoint_does_not_queue_or_send() -> None:
    with patch("ai_trading_lab.runtime.notifications.NotificationStore") as store:
        await queue_checkpoint(
            AsyncMock(), {"state": {"entry_suspended": False}}, datetime.now(UTC)
        )
        store.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,kind", [("DEGRADED", "failure"), ("STOPPED", "safety_halt"), ("PAUSED", "safety_halt")]
)
async def test_suspended_checkpoint_queues_stable_diagnostic_without_raw_content(
    status: str, kind: str
) -> None:
    checkpoint = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "sequence": 3,
        "last_at": "2026-10-20T00:00:00+00:00",
        "state": {"entry_suspended": True, "status": status, "history": ["private raw body"]},
    }
    store = AsyncMock()
    with patch("ai_trading_lab.runtime.notifications.NotificationStore", return_value=store):
        await queue_checkpoint(AsyncMock(), checkpoint, datetime(2026, 10, 20, tzinfo=UTC))
    event = store.enqueue.await_args.args[0]
    assert event.kind == kind
    assert event.event_key == "runtime:00000000-0000-0000-0000-000000000001:3"
    assert "private" not in event.text
    assert "not assumed liquidated" in event.text


@pytest.mark.asyncio
async def test_queue_failure_is_diagnostic_not_a_control_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    store = AsyncMock()
    store.enqueue.side_effect = SQLAlchemyError("private database credential")
    checkpoint = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "sequence": 3,
        "last_at": "2026-10-20T00:00:00+00:00",
        "state": {"entry_suspended": True, "status": "DEGRADED"},
    }
    with patch("ai_trading_lab.runtime.notifications.NotificationStore", return_value=store):
        await queue_checkpoint(AsyncMock(), checkpoint, datetime(2026, 10, 20, tzinfo=UTC))
    output = capsys.readouterr()
    assert "notification_queue" in output.err and "private" not in output.err


@pytest.mark.asyncio
async def test_recovery_notice_is_stable_idempotent_and_free_of_raw_content() -> None:
    from ai_trading_lab.runtime.notifications import queue_recovery

    checkpoint = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "sequence": 9,
        "last_at": "2026-10-20T00:00:00+00:00",
        "state": {"auto_clears": 2, "auto_clear_breaker": False, "history": ["private raw body"]},
    }
    store = AsyncMock()
    with patch("ai_trading_lab.runtime.notifications.NotificationStore", return_value=store):
        await queue_recovery(AsyncMock(), checkpoint, datetime(2026, 10, 20, tzinfo=UTC))
    event = store.enqueue.await_args.args[0]
    assert event.kind == "recovery"
    assert event.event_key == "runtime:00000000-0000-0000-0000-000000000001:recovered:2"
    assert "private" not in event.text and "not cleared" in event.text
