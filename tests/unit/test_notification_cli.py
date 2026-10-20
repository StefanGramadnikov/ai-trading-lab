import argparse
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from ai_trading_lab.config import Settings
from ai_trading_lab.reporting import cli


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["show", "recover-unknown", "send"])
async def test_explicit_commands_cleanup_and_disable_real_network(
    command: str, capsys: Any
) -> None:
    engine = AsyncMock()
    store = AsyncMock()
    store.get.return_value = {"state": "pending"}
    store.recover_unknown.return_value = {"state": "unknown"}
    args = argparse.Namespace(
        command=command, event_key="test-key", at=datetime(2026, 10, 20, tzinfo=UTC)
    )
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "NotificationStore", return_value=store),
    ):
        if command == "send":
            with pytest.raises(ValueError, match="disabled"):
                await cli.run(args)
            store.dispatch.assert_not_awaited()
        else:
            assert await cli.run(args) == 0
            assert "state" in capsys.readouterr().out
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_only_uses_explicit_adapter_and_target() -> None:
    settings = Settings(
        _env_file=None,
        telegram_enabled=True,
        telegram_bot_token=SecretStr("12345:" + "x" * 25),
        telegram_chat_id=SecretStr("123"),
    )
    engine, store = AsyncMock(), AsyncMock()
    store.dispatch.return_value = {"state": "delivered", "message_id": 1}
    with (
        patch.object(cli, "Settings", return_value=settings),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "NotificationStore", return_value=store),
        patch.object(cli, "TelegramAdapter") as adapter,
    ):
        assert (
            await cli.run(
                argparse.Namespace(
                    command="send", event_key="test", at=datetime(2026, 10, 20, tzinfo=UTC)
                )
            )
            == 0
        )
        adapter.assert_called_once_with(
            settings.telegram_bot_token, settings.telegram_chat_id, enabled=True
        )
        store.dispatch.assert_awaited_once()
    engine.dispose.assert_awaited_once()


def test_main_sanitizes_failures(capsys: Any) -> None:
    with (
        patch.object(cli, "parser") as parser,
        patch.object(cli, "run", AsyncMock(side_effect=ValueError("secret-token"))),
    ):
        parser.return_value.parse_args.return_value = argparse.Namespace()
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    assert "secret-token" not in capsys.readouterr().out
