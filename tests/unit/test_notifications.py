from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.telegram import TelegramAdapter


@pytest.mark.parametrize("kind", ["failure", "safety_halt", "budget_warning", "daily_summary"])
def test_notification_plain_summary(kind: str) -> None:
    value = Notification.model_validate(
        dict(
            event_key="run:failure",
            kind=kind,
            occurred_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
            text="Fixture only <b>text</b>",
        )
    )
    assert "PAPER ONLY" in value.message()
    assert value.message().endswith("Fixture only <b>text</b>")


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "trade"},
        {"event_key": ""},
        {"event_key": "x/y"},
        {"text": ""},
        {"text": " "},
        {"text": "x" * 3001},
        {"text": "💡" * 1800},
        {"text": "x\x00"},
        {"text": "sk-abcdefghijklmnop"},
        {"text": "123456:abcdefghijklmnopqrstuv"},
        {"occurred_at_utc": datetime(2026, 1, 1)},
        {"unknown": "field"},
    ],
)
def test_invalid_notification(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Notification.model_validate(
            dict(
                event_key="a",
                kind="failure",
                occurred_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
                text="ok",
            )
            | change
        )


@pytest.mark.parametrize(
    "token,chat,timeout",
    [
        ("bad", "1", 1),
        ("123:abcdefghijklmnopqrst", "@username", 1),
        ("123:abcdefghijklmnopqrst", "1", 0),
        ("123:abcdefghijklmnopqrst", "1", 11),
    ],
)
def test_adapter_configuration(token: str, chat: str, timeout: float) -> None:
    with pytest.raises(ValueError):
        TelegramAdapter(SecretStr(token), SecretStr(chat), timeout_seconds=timeout)
