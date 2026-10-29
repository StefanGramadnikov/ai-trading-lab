"""The alerts command line: configuration, the chat-id helper, and that the token never leaks."""

import json

import httpx2
import pytest
from pydantic import SecretStr

from ai_trading_lab.alerts import cli
from ai_trading_lab.config import Settings

TOKEN = "123456:fixture_only_abcdefghijklmnopqrst"


def settings(**extra: object) -> Settings:
    return Settings(_env_file=None, **extra)  # type: ignore[arg-type]


def test_alerts_stay_off_until_telegram_is_enabled_with_a_token_and_a_chat() -> None:
    assert cli.adapter_from(settings()) is None
    assert cli.adapter_from(settings(telegram_bot_token=SecretStr(TOKEN))) is None
    on = settings(
        telegram_enabled=True, telegram_bot_token=SecretStr(TOKEN), telegram_chat_id=SecretStr("42")
    )
    adapter = cli.adapter_from(on)
    assert adapter is not None and adapter.enabled


def test_enabling_telegram_without_its_values_is_refused_by_the_settings() -> None:
    with pytest.raises(ValueError):
        settings(telegram_enabled=True)


@pytest.mark.asyncio
async def test_find_chat_lists_each_chat_once_and_never_prints_the_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    updates = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 42, "type": "private", "first_name": "Stefan"}}},
            {"message": {"chat": {"id": 42, "type": "private", "first_name": "Stefan"}}},
            {"edited_message": {"chat": {"id": 7}}},
        ],
    }

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "api.telegram.org" and request.url.path.endswith("/getUpdates")
        return httpx2.Response(200, json=updates)

    code = await cli.find_chat(
        settings(telegram_bot_token=SecretStr(TOKEN)), httpx2.MockTransport(handle)
    )
    out = capsys.readouterr().out
    assert code == 0 and TOKEN not in out
    assert json.loads(out) == {"chats": [{"id": "42", "type": "private", "name": "Stefan"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply,expected",
    [
        (httpx2.Response(200, json={"ok": True, "result": []}), 1),
        (httpx2.Response(401, json={"ok": False, "description": TOKEN}), 2),
        (httpx2.Response(200, text="not json"), 2),
    ],
)
async def test_find_chat_reports_no_chat_a_refused_token_and_garbage_without_the_token(
    reply: httpx2.Response, expected: int, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli.find_chat(
        settings(telegram_bot_token=SecretStr(TOKEN)),
        httpx2.MockTransport(lambda request: reply),
    )
    assert code == expected and TOKEN not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_find_chat_without_a_token_says_what_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await cli.find_chat(settings()) == 2
    assert "ATL_TELEGRAM_BOT_TOKEN" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_find_chat_survives_a_network_failure_without_leaking_the_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    code = await cli.find_chat(
        settings(telegram_bot_token=SecretStr(TOKEN)), httpx2.MockTransport(boom)
    )
    assert code == 2 and TOKEN not in capsys.readouterr().out


def test_the_parser_knows_run_test_and_find_chat() -> None:
    parsed = cli.parser().parse_args(["run", "--once"])
    assert (parsed.command, parsed.once) == ("run", True)
    assert cli.parser().parse_args(["test"]).command == "test"
    assert cli.parser().parse_args(["find-chat"]).command == "find-chat"
