from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid5

from pydantic import Field, JsonValue, model_validator

from ai_trading_lab.market_data.models import Capture, DataMode, FrozenModel, Source, TransportError
from ai_trading_lab.market_data.transport import validate_request


class CaptureArchive(FrozenModel):
    schema_version: Literal["1"] = "1"
    source: Source
    mint_addresses: tuple[str, ...] = ()
    limit: int = Field(default=5, ge=1, le=10)
    max_pages: int = Field(default=2, ge=1, le=3)
    captures: Annotated[tuple[Capture, ...], Field(max_length=30)]

    @model_validator(mode="after")
    def validate_captures(self) -> CaptureArchive:
        if len(self.mint_addresses) > 10:
            raise ValueError("Too many mint addresses")
        ids = set()
        for capture in self.captures:
            if capture.source != self.source or capture.capture_id in ids:
                raise ValueError("Archive source mismatch or duplicate capture ID")
            if len(capture.body.encode()) > 2_000_000:
                raise ValueError("Archive capture exceeds response size limit")
            validate_request(
                capture.source,
                capture.method,
                capture.path,
                params=capture.params,
                json_body=capture.request_body,
            )
            ids.add(capture.capture_id)
        return self


class ReplayTransport:
    """Strict ordered replay with no HTTP client or network fallback."""

    def __init__(self, archive: CaptureArchive) -> None:
        self._captures = archive.captures
        self.request_count = 0

    @property
    def remaining(self) -> int:
        return len(self._captures) - self.request_count

    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture:
        validate_request(source, method, path, params=params, json_body=json_body)
        if self.request_count >= len(self._captures):
            raise TransportError(source, "replay_exhausted")
        capture = self._captures[self.request_count]
        if (
            capture.source != source
            or capture.method != method
            or capture.path != path
            or capture.params != (params or {})
            or capture.request_body != json_body
        ):
            raise TransportError(source, "replay_request_mismatch")
        self.request_count += 1
        if capture.status_code != 200:
            raise TransportError(
                source,
                "replayed_http_error",
                capture.model_copy(
                    update={
                        "capture_id": uuid5(capture.capture_id, "REPLAY"),
                        "data_mode": DataMode.REPLAY,
                        "origin_capture_id": capture.origin_capture_id or capture.capture_id,
                    }
                ),
            )
        return capture.model_copy(
            update={
                "capture_id": uuid5(capture.capture_id, "REPLAY"),
                "data_mode": DataMode.REPLAY,
                "origin_capture_id": capture.origin_capture_id or capture.capture_id,
            }
        )
