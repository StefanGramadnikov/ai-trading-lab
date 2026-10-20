"""Queue durable runtime diagnostics locally; never deliver Telegram from the worker."""

import json
import sys
from datetime import datetime
from typing import cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.store import NotificationStore


async def queue_checkpoint(
    engine: AsyncEngine, checkpoint: dict[str, object], received: datetime
) -> None:
    state = cast(dict[str, object], checkpoint["state"])
    if not state.get("entry_suspended"):
        return
    event = Notification(
        event_key=f"runtime:{checkpoint['session_id']}:{checkpoint['sequence']}",
        kind="failure" if state.get("status") == "DEGRADED" else "safety_halt",
        occurred_at_utc=datetime.fromisoformat(str(checkpoint["last_at"])),
        text=(
            f"Runtime {checkpoint['session_id']}; sequence {checkpoint['sequence']}; "
            f"recorded state {state.get('status')}; entries suspended. "
            "Inspect the local checkpoint. Positions are not assumed liquidated."
        ),
    )
    try:
        await NotificationStore(engine).enqueue(event, received)
    except (SQLAlchemyError, TimeoutError, OSError, ValueError):
        # Checkpoint truth survives an alert subsystem failure. Never abandon a
        # qualified exit/mark cycle because its optional diagnostic cannot queue.
        print(
            json.dumps(
                {
                    "status": "warning",
                    "component": "notification_queue",
                    "error": "Local notification unavailable; inspect runtime checkpoint",
                }
            ),
            file=sys.stderr,
        )
