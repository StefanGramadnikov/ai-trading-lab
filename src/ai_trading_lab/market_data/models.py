from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Never, Protocol, cast
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


def parse_json(text: str) -> object:
    """Parse exact numbers and bound untrusted structure before normalization."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant: {value}")

    try:
        payload = cast(
            object, json.loads(text, parse_float=Decimal, parse_constant=reject_constant)
        )
    except RecursionError:
        raise ValueError("JSON nesting exceeds safe depth") from None
    pending = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth >= 64:
                raise ValueError("JSON nesting exceeds safe depth")
            children = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in children)
    return payload


class Source(StrEnum):
    DEXSCREENER = "dexscreener"
    SOLANA = "solana"
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class DataMode(StrEnum):
    OBSERVED = "OBSERVED"
    FIXTURE = "FIXTURE"
    REPLAY = "REPLAY"


class DataQuality(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"


def _reject_mutation(*args: object, **kwargs: object) -> Never:
    raise TypeError("Evidence containers are immutable")


class FrozenDict(dict[str, JsonValue]):
    """JSON-compatible evidence that cannot change after validation."""

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _reject_mutation
    __ior__ = _reject_mutation

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenDict:
        return self


class FrozenList(list[JsonValue]):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _reject_mutation
    reverse = sort = __iadd__ = __imul__ = _reject_mutation

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenList:
        return self


def freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return FrozenDict({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze_json(item) for item in value)
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )


class Capture(FrozenModel):
    capture_id: UUID = Field(default_factory=uuid4)
    source: Source
    method: Literal["GET", "POST"]
    path: str
    params: dict[str, str] = Field(default_factory=dict)
    request_body: dict[str, JsonValue] | None = None
    body: str
    status_code: int = Field(default=200, ge=100, le=599)
    received_at_utc: AwareDatetime
    data_mode: DataMode
    origin_capture_id: UUID | None = None
    schema_version: str = "1"

    @field_validator("params", "request_body")
    @classmethod
    def freeze_request(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return cast(dict[str, JsonValue], freeze_json(value)) if value is not None else None

    @field_validator("received_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    def payload(self) -> object:
        return parse_json(self.body)


class Instrument(FrozenModel):
    source: Source
    venue: str = Field(min_length=1)
    native_id: str = Field(min_length=1)
    kind: Literal["spot", "token", "prediction"]
    base_asset: str | None = None
    quote_currency: str | None = None
    market_id: str | None = None
    outcome: str | None = None
    title: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def freeze_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], freeze_json(value))

    @property
    def definition_hash(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()


class PriceLevel(FrozenModel):
    price: Decimal = Field(ge=0)
    quantity: Decimal = Field(gt=0)
    derived: bool = False


class Observation(FrozenModel):
    capture_id: UUID
    instrument: Instrument
    event_time_utc: AwareDatetime | None = None
    received_at_utc: AwareDatetime
    quality: DataQuality = DataQuality.PARTIAL
    issues: tuple[str, ...] = ()
    price: Decimal | None = Field(default=None, ge=0)
    price_currency: str | None = None
    liquidity: Decimal | None = Field(default=None, ge=0)
    liquidity_currency: str | None = None
    bids: tuple[PriceLevel, ...] = ()
    asks: tuple[PriceLevel, ...] = ()
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: str = "1"

    @field_validator("facts")
    @classmethod
    def freeze_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], freeze_json(value))

    @field_validator("received_at_utc", "event_time_utc")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def units(self) -> Observation:
        if self.price is not None and not self.price_currency:
            raise ValueError("Price requires explicit currency/unit")
        if self.liquidity is not None and not self.liquidity_currency:
            raise ValueError("Liquidity requires explicit currency/unit")
        return self

    def assessed(
        self, *, max_age_seconds: float = 60, future_skew_seconds: float = 5
    ) -> Observation:
        if self.quality == DataQuality.INVALID:
            return self
        if self.event_time_utc is None:
            return self.model_copy(
                update={
                    "quality": DataQuality.PARTIAL,
                    "issues": tuple(dict.fromkeys((*self.issues, "source_time_unknown"))),
                }
            )
        age = self.received_at_utc - self.event_time_utc
        if age < -timedelta(seconds=future_skew_seconds):
            return self.model_copy(
                update={
                    "quality": DataQuality.INVALID,
                    "issues": tuple(dict.fromkeys((*self.issues, "source_time_in_future"))),
                }
            )
        if age > timedelta(seconds=max_age_seconds):
            return self.model_copy(
                update={
                    "quality": DataQuality.STALE,
                    "issues": tuple(dict.fromkeys((*self.issues, "source_time_stale"))),
                }
            )
        return self


class Failure(FrozenModel):
    source: Source
    target: str
    code: str
    message: str


class Batch(FrozenModel):
    captures: tuple[Capture, ...] = ()
    observations: tuple[Observation, ...] = ()
    failures: tuple[Failure, ...] = ()

    @classmethod
    def combine(cls, batches: Sequence[Batch]) -> Batch:
        return cls(
            captures=tuple(c for b in batches for c in b.captures),
            observations=tuple(o for b in batches for o in b.observations),
            failures=tuple(f for b in batches for f in b.failures),
        )


class TransportError(Exception):
    def __init__(self, source: Source, code: str, capture: Capture | None = None) -> None:
        super().__init__(f"{source.value}: {code}")
        self.source = source
        self.code = code
        self.capture = capture


class Transport(Protocol):
    async def request(
        self,
        source: Source,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, JsonValue] | None = None,
    ) -> Capture: ...
