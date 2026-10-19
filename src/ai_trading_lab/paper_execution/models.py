"""Immutable contracts for explicitly configured, paper-only execution."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, field_validator, model_validator

from ai_trading_lab.market_data.models import (
    DataMode,
    DataQuality,
    FrozenModel,
    PriceLevel,
    Source,
)

MAX_AMOUNT = Decimal("1e18")


def decimal_amount(value: object) -> Decimal:
    """Reject binary floats and values outside the fixed-point storage envelope."""
    if isinstance(value, (float, bool)) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("amount_requires_exact_decimal")
    if isinstance(value, str) and len(value) > 200:
        raise ValueError("amount_representation_exceeded")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid_amount") from exc
    if not amount.is_finite() or amount.copy_abs() > MAX_AMOUNT:
        raise ValueError("amount_out_of_bounds")
    # Inspect the tuple rather than normalize() under the caller's decimal context.
    digits = amount.as_tuple().digits
    exponent = int(amount.as_tuple().exponent)
    if len(digits) > 100 or abs(exponent) > 100:
        raise ValueError("amount_representation_exceeded")
    if not amount:
        return Decimal(0)
    trailing = 0
    for digit in reversed(digits):
        if digit:
            break
        trailing += 1
    if amount and exponent + trailing < -18:
        raise ValueError("amount_precision_exceeded")
    return amount


Amount = Annotated[Decimal, BeforeValidator(decimal_amount)]
PositiveAmount = Annotated[Amount, Field(gt=0)]
NonnegativeAmount = Annotated[Amount, Field(ge=0)]


def _hash(model: FrozenModel) -> str:
    def canonical(value: object) -> object:
        if isinstance(value, Decimal):
            text = format(value, "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        if isinstance(value, dict):
            return {str(key): canonical(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        if isinstance(value, (UUID, datetime)):
            return str(value)
        return value

    data = json.dumps(canonical(model.model_dump()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


class PaperInstrument(FrozenModel):
    source: Source
    venue: str = Field(min_length=1, max_length=200)
    native_id: str = Field(min_length=1, max_length=500)
    kind: Literal["spot", "prediction"]
    base_unit: str = Field(min_length=1, max_length=200)
    cash_unit: str = Field(min_length=1, max_length=200)
    market_id: str | None = Field(default=None, min_length=1, max_length=500)
    outcome: str | None = Field(default=None, min_length=1, max_length=500)
    tick_size: PositiveAmount
    quantity_step: PositiveAmount
    cash_step: PositiveAmount

    @model_validator(mode="after")
    def identity(self) -> PaperInstrument:
        if self.base_unit == self.cash_unit:
            raise ValueError("distinct_units_required")
        if self.kind == "prediction" and (not self.market_id or not self.outcome):
            raise ValueError("prediction_identity_required")
        if self.kind == "prediction" and self.tick_size > 1:
            raise ValueError("prediction_tick_out_of_bounds")
        return self

    @property
    def key(self) -> str:
        return _hash(self)


class SimulationPolicy(FrozenModel):
    version: str = Field(min_length=1, max_length=200)
    fee_bps: NonnegativeAmount = Field(le=10000)
    latency_ms: int = Field(ge=0, le=60000, strict=True)
    max_age_seconds: int = Field(ge=0, le=86400, strict=True)
    future_skew_seconds: int = Field(ge=0, le=60, strict=True)
    max_order_notional: PositiveAmount
    max_position_notional: PositiveAmount
    max_total_exposure: PositiveAmount
    max_cost_bps: NonnegativeAmount = Field(le=10000)


class OrderIntent(FrozenModel):
    portfolio_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    instrument: PaperInstrument
    side: Literal["buy", "sell"]
    quantity: PositiveAmount
    limit_price: PositiveAmount
    created_at_utc: AwareDatetime
    expires_at_utc: AwareDatetime

    @field_validator("created_at_utc", "expires_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def expiry(self) -> OrderIntent:
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("expiry_must_follow_creation")
        if self.instrument.kind == "prediction" and self.limit_price > 1:
            raise ValueError("prediction_price_out_of_bounds")
        return self

    @property
    def fingerprint(self) -> str:
        return _hash(self)


class ExecutionQuote(FrozenModel):
    snapshot_id: UUID
    instrument: PaperInstrument
    event_time_utc: AwareDatetime | None
    received_at_utc: AwareDatetime
    quality: DataQuality
    data_mode: DataMode
    bids: tuple[PriceLevel, ...] = Field(default=(), max_length=100)
    asks: tuple[PriceLevel, ...] = Field(default=(), max_length=100)
    spot_price: PositiveAmount | None = None
    spot_capacity: PositiveAmount | None = None
    impact_bps: NonnegativeAmount | None = Field(default=None, le=10000)

    @field_validator("bids", "asks", mode="before")
    @classmethod
    def exact_levels(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            for level in value:
                if isinstance(level, dict):
                    decimal_amount(level.get("price"))
                    decimal_amount(level.get("quantity"))
        return value

    @field_validator("event_time_utc", "received_at_utc")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def levels(self) -> ExecutionQuote:
        for level in (*self.bids, *self.asks):
            decimal_amount(level.price)
            decimal_amount(level.quantity)
            if self.instrument.kind == "prediction" and level.price > 1:
                raise ValueError("prediction_price_out_of_bounds")
        if self.instrument.kind == "prediction" and self.spot_price is not None:
            if self.spot_price > 1:
                raise ValueError("prediction_price_out_of_bounds")
        return self


class SimulationResult(FrozenModel):
    status: Literal["FILLED", "PARTIALLY_FILLED", "CANCELLED", "REJECTED", "EXPIRED"]
    reason: str
    quantity: NonnegativeAmount
    notional: NonnegativeAmount
    fee: NonnegativeAmount
    consumed: tuple[NonnegativeAmount, ...]
    executed_at_utc: AwareDatetime

    @field_validator("executed_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
