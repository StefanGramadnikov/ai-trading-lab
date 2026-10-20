"""Bounded operational summaries; callers must never pass prompts or credentials."""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kind = Literal["failure", "safety_halt", "budget_warning", "daily_summary"]
State = Literal["pending", "delivering", "delivered", "rejected", "unknown"]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(UTC)


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    event_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9:._-]+$")
    kind: Kind
    occurred_at_utc: datetime
    text: str = Field(min_length=1, max_length=3000)

    _utc = field_validator("occurred_at_utc")(utc)

    @field_validator("text")
    @classmethod
    def safe_summary(cls, value: str) -> str:
        if (
            not value.strip()
            or len(value.encode("utf-16-le")) // 2 > 3500
            or any(ord(c) < 32 and c != "\n" for c in value)
        ):
            raise ValueError("Plain operational text required")
        if re.search(r"sk-[A-Za-z0-9_-]{12,}|\d{5,}:[A-Za-z0-9_-]{20,}|PRIVATE KEY", value):
            raise ValueError("Potential secret refused")
        return value

    def message(self) -> str:
        return f"AI Trading Lab | PAPER ONLY | {self.kind}\n{self.text}"


class DeliveryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    state: Literal["delivered", "rejected", "unknown"]
    reason: str
    message_id: int | None = Field(default=None, gt=0)
