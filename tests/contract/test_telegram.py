"""Synthetic official sendMessage contracts, never real Telegram requests."""

import json
from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import SecretStr

from ai_trading_lab.notifications.models import Notification
from ai_trading_lab.notifications.telegram import TelegramAdapter

TOKEN = SecretStr("123456:fixture_only_abcdefghijklmnopqrst")
CHAT = SecretStr("-123456")
EVENT = Notification(
    event_key="fixture:failure",
    kind="failure",
    occurred_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
    text="Fixture failed",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,state",
    [
        (200, {"ok": True, "result": {"message_id": 7, "chat": {"id": -123456}}}, "delivered"),
        (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}, "rejected"),
        (401, {"ok": False, "description": "secret provider text"}, "rejected"),
        (500, {"ok": False}, "unknown"),
        (302, {}, "unknown"),
        (200, {"ok": True, "result": {"message_id": True, "chat": {"id": -123456}}}, "unknown"),
        (200, {"ok": True, "result": {"message_id": 7, "chat": {"id": 9}}}, "unknown"),
        (200, {"ok": True}, "unknown"),
        (200, [], "unknown"),
        (200, {"ok": False}, "unknown"),
    ],
)
async def test_response_validation_one_attempt(status: int, payload: object, state: str) -> None:
    calls = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        assert request.method == "POST" and request.url.host == "api.telegram.org"
        assert request.url.path.endswith("/sendMessage")
        body = json.loads(request.content)
        assert body["chat_id"] == "-123456" and "PAPER ONLY" in body["text"]
        assert "parse_mode" not in body
        assert body["link_preview_options"] == {"is_disabled": True}
        return httpx2.Response(status, json=payload)

    adapter = TelegramAdapter(TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(handle))
    result = await adapter.send(EVENT)
    assert result.state == state and len(calls) == 1
    assert "secret" not in result.model_dump_json()
    assert result.message_id == (7 if state == "delivered" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "malformed", "oversize", "unicode"])
async def test_uncertain_failures_do_not_retry(failure: str) -> None:
    calls = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if failure == "timeout":
            raise httpx2.ReadTimeout("contains sensitive URL", request=request)
        raw = {"malformed": b"{", "oversize": b"x" * 20000, "unicode": b"\xff"}[failure]
        return httpx2.Response(200, content=raw)

    result = await TelegramAdapter(
        TOKEN, CHAT, enabled=True, transport=httpx2.MockTransport(handle)
    ).send(EVENT)
    assert result.state == "unknown" and len(calls) == 1
    assert "sensitive" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_disabled_never_sends() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("Disabled adapter attempted network")

    result = await TelegramAdapter(TOKEN, CHAT, transport=httpx2.MockTransport(handle)).send(EVENT)
    assert result.reason == "disabled"


@pytest.mark.asyncio
async def test_total_deadline_includes_waiting_for_response() -> None:
    import asyncio

    calls = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        await asyncio.Event().wait()
        raise AssertionError("Unreachable")

    result = await TelegramAdapter(
        TOKEN, CHAT, enabled=True, timeout_seconds=0.001, transport=httpx2.MockTransport(handle)
    ).send(EVENT)
    assert result.state == "unknown" and len(calls) == 1
