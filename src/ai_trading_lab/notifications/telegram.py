"""Fixed official Telegram destination; one attempt, no ambiguous-result retry."""

import asyncio
import hashlib
import hmac
import re

import httpx2
from pydantic import SecretStr

from ai_trading_lab.market_data.models import parse_json
from ai_trading_lab.notifications.models import DeliveryResult, Notification


class TelegramAdapter:
    def __init__(
        self,
        token: SecretStr,
        chat_id: SecretStr,
        *,
        enabled: bool = False,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5,
    ) -> None:
        if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,100}", token.get_secret_value()):
            raise ValueError("Invalid Telegram token shape")
        if not re.fullmatch(r"-?[1-9][0-9]{0,19}", chat_id.get_secret_value()):
            raise ValueError("Numeric Telegram chat ID required")
        if not 0 < timeout_seconds <= 10:
            raise ValueError("Invalid Telegram deadline")
        self.enabled = enabled
        self._token = token
        self._chat = chat_id
        self._transport = transport
        self._timeout = timeout_seconds

    @property
    def destination_hash(self) -> str:
        """Identify bot and chat without persisting credentials or recipient ID.

        Chat IDs are short numbers, so an unkeyed digest would be brute-forceable from a
        database dump. The bot token is a secret that is never stored; keying with it makes
        the digest useless to anyone holding only persisted data.
        """
        token = self._token.get_secret_value()
        return hmac.new(
            token.encode(), f"destination:{self._chat.get_secret_value()}".encode(), hashlib.sha256
        ).hexdigest()

    async def send(self, event: Notification) -> DeliveryResult:
        if not self.enabled:
            return DeliveryResult(state="rejected", reason="disabled")
        url = f"https://api.telegram.org/bot{self._token.get_secret_value()}/sendMessage"
        try:
            async with (
                asyncio.timeout(self._timeout),
                httpx2.AsyncClient(
                    transport=self._transport,
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    url,
                    json={
                        "chat_id": self._chat.get_secret_value(),
                        "text": event.message(),
                        "link_preview_options": {"is_disabled": True},
                        "protect_content": True,
                    },
                ) as response,
            ):
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return DeliveryResult(state="unknown", reason="response_too_large")
                if response.status_code >= 500 or 300 <= response.status_code < 400:
                    return DeliveryResult(state="unknown", reason="http_uncertain")
                data = parse_json(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("object_required")
                if data.get("ok") is False and 400 <= response.status_code < 500:
                    return DeliveryResult(state="rejected", reason="provider_rejected")
                result = data.get("result")
                if response.status_code != 200 or data.get("ok") is not True:
                    raise ValueError("invalid_result")
                if not isinstance(result, dict):
                    raise ValueError("message_required")
                message_id = result.get("message_id")
                chat = result.get("chat")
                if (
                    type(message_id) is not int
                    or message_id <= 0
                    or not isinstance(chat, dict)
                    or type(chat.get("id")) is not int
                    or str(chat["id"]) != self._chat.get_secret_value()
                ):
                    raise ValueError("invalid_message")
                return DeliveryResult(state="delivered", reason="confirmed", message_id=message_id)
        except (httpx2.HTTPError, TimeoutError, OSError, ValueError, UnicodeError):
            # Never retain provider text, exception URLs (contain token), or raw bodies.
            return DeliveryResult(state="unknown", reason="delivery_uncertain")
