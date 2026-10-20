"""Immutable operational parameters; no implicit capital or risk choices."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.experiments.models import RiskProfile
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.paper_execution.models import PaperInstrument, PositiveAmount, SimulationPolicy
from ai_trading_lab.strategies.models import StrategyConfig


class RuntimeConfig(FrozenModel):
    session_id: UUID
    cash_unit: str = Field(min_length=1, max_length=200)
    cash_step: PositiveAmount
    initial_cash: PositiveAmount
    simulation: SimulationPolicy
    profile: RiskProfile
    strategy: StrategyConfig
    order_notional: PositiveAmount
    order_ttl_seconds: int = Field(ge=1, le=86400, strict=True)
    data_mode: Literal["FIXTURE", "OBSERVED"]
    started_at_utc: AwareDatetime
    deadline_at_utc: AwareDatetime
    poll_interval_seconds: int = Field(ge=1, le=3600, strict=True)
    max_gap_seconds: int = Field(ge=1, le=604800, strict=True)
    max_cycles: int = Field(ge=1, le=10000, strict=True)
    max_journal_bytes: int = Field(ge=1, le=64 * 1024 * 1024, strict=True)
    instrument: PaperInstrument | None = None

    @field_validator("started_at_utc", "deadline_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded(self) -> "RuntimeConfig":
        if self.deadline_at_utc <= self.started_at_utc:
            raise ValueError("runtime_deadline_must_follow_start")
        if self.deadline_at_utc - self.started_at_utc > timedelta(days=4):
            raise ValueError("runtime_duration_cap")
        if self.instrument is not None and (
            self.instrument.cash_unit,
            self.instrument.cash_step,
        ) != (self.cash_unit, self.cash_step):
            raise ValueError("runtime_instrument_currency_mismatch")
        if self.initial_cash % self.cash_step:
            raise ValueError("runtime_initial_cash_step")
        if self.data_mode == "OBSERVED" and self.instrument is None:
            raise ValueError("runtime_observed_instrument_required")
        return self
