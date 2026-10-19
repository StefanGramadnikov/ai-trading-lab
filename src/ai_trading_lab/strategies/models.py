"""Immutable research parameters and explicitly available signal evidence."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator

from ai_trading_lab.market_data.models import DataMode, FrozenModel
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    NonnegativeAmount,
    PositiveAmount,
)


class TimedEvidence(FrozenModel):
    instrument_key: str = Field(min_length=64, max_length=64)
    data_mode: DataMode
    event_time_utc: AwareDatetime
    available_at_utc: AwareDatetime

    @field_validator("event_time_utc", "available_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class PricePoint(TimedEvidence):
    price: PositiveAmount


class MomentumFeatures(TimedEvidence):
    volume_6h: NonnegativeAmount | None = None
    volume_24h: PositiveAmount | None = None
    pool_created_at_utc: AwareDatetime | None = None
    liquidity: NonnegativeAmount | None = None
    liquidity_unit: str | None = Field(default=None, min_length=1, max_length=200)
    verified_owner_share: NonnegativeAmount | None = Field(default=None, le=1)
    ownership_basis: Literal["unique_owner_balances"] | None = None

    @field_validator("pool_created_at_utc")
    @classmethod
    def pool_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value else None


class Forecast(FrozenModel):
    instrument_key: str = Field(min_length=64, max_length=64)
    market_id: str = Field(min_length=1, max_length=500)
    outcome: str = Field(min_length=1, max_length=500)
    resolution_terms_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    probability: NonnegativeAmount = Field(le=1)
    provenance: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=200)
    calibration_reference: str = Field(min_length=1, max_length=500)
    independent: bool = Field(strict=True)
    data_mode: DataMode
    produced_at_utc: AwareDatetime
    available_at_utc: AwareDatetime

    @field_validator("produced_at_utc", "available_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class StrategyFrame(FrozenModel):
    quote: ExecutionQuote
    at_utc: AwareDatetime
    history: tuple[PricePoint, ...] = Field(default=(), max_length=100)
    momentum: MomentumFeatures | None = None
    forecast: Forecast | None = None
    resolution_terms_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class MomentumConfig(FrozenModel):
    kind: Literal["momentum"] = "momentum"
    version: str = Field(min_length=1, max_length=200)
    max_age_seconds: int = Field(ge=0, le=86400, strict=True)
    lookback_seconds: int = Field(gt=0, le=604800, strict=True)
    min_momentum_bps: NonnegativeAmount
    min_volume_ratio: NonnegativeAmount
    max_pool_age_seconds: int = Field(gt=0, le=31536000, strict=True)
    min_liquidity: PositiveAmount
    max_owner_share: NonnegativeAmount | None = Field(le=1)
    exit_volume_ratio: NonnegativeAmount


class ProbabilityConfig(FrozenModel):
    kind: Literal["probability"] = "probability"
    version: str = Field(min_length=1, max_length=200)
    max_age_seconds: int = Field(ge=0, le=86400, strict=True)
    min_edge: NonnegativeAmount = Field(le=1)
    cost_buffer: NonnegativeAmount = Field(le=1)


class CashConfig(FrozenModel):
    kind: Literal["cash"] = "cash"
    version: str = Field(min_length=1, max_length=200)


StrategyConfig = Annotated[
    MomentumConfig | ProbabilityConfig | CashConfig, Field(discriminator="kind")
]


class TradeProposal(FrozenModel):
    kind: Literal["proposal"] = "proposal"
    side: Literal["buy", "sell"]
    limit_price: PositiveAmount
    reason: str = Field(min_length=1, max_length=200)
    evidence: tuple[str, ...] = Field(max_length=100)


class Abstain(FrozenModel):
    kind: Literal["abstain"] = "abstain"
    reason: str = Field(min_length=1, max_length=200)
    evidence: tuple[str, ...] = Field(default=(), max_length=100)
