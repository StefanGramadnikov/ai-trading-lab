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


def test_f21_destination_hash_is_keyed_by_the_unstored_token() -> None:
    import hashlib

    def adapter(token: str, chat: str = "123456789") -> TelegramAdapter:
        return TelegramAdapter(SecretStr(token), SecretStr(chat))

    first = adapter("123456:abcdefghijklmnopqrstuvwxyz")
    other_secret = adapter("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    other_chat = adapter("123456:abcdefghijklmnopqrstuvwxyz", "987654321")
    assert first.destination_hash == adapter("123456:abcdefghijklmnopqrstuvwxyz").destination_hash
    assert (
        len({first.destination_hash, other_secret.destination_hash, other_chat.destination_hash})
        == 3
    )
    # Anyone holding only the persisted digest cannot confirm a guessed bot:chat pair.
    assert first.destination_hash != hashlib.sha256(b"123456:123456789").hexdigest()
