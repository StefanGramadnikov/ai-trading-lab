"""Immutable operational parameters; no implicit capital or risk choices."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.experiments.models import RiskProfile
from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.paper_execution.models import PaperInstrument, PositiveAmount, SimulationPolicy
from ai_trading_lab.runtime.capacity import (
    CREATION_RESERVE_BYTES,
    MAX_JOURNAL_BYTES,
    MIN_EVENT_BYTES,
    TERMINAL_RESERVE_BYTES,
    cycle_overhead,
)
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
    max_gap_seconds: int = Field(ge=1, le=345600, strict=True)
    max_cycles: int = Field(ge=1, le=10000, strict=True)
    max_journal_bytes: int = Field(ge=1, le=MAX_JOURNAL_BYTES, strict=True)
    instrument: PaperInstrument | None = None
    max_event_bytes: int | None = Field(
        default=None, ge=MIN_EVENT_BYTES, le=MAX_JOURNAL_BYTES, strict=True
    )

    @field_validator("started_at_utc", "deadline_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded(self) -> "RuntimeConfig":
        available = self.max_journal_bytes - TERMINAL_RESERVE_BYTES - CREATION_RESERVE_BYTES
        derived = available // self.max_cycles - cycle_overhead()
        envelope = self.max_event_bytes if self.max_event_bytes is not None else derived
        if (
            envelope < MIN_EVENT_BYTES
            or self.max_cycles * (envelope + cycle_overhead()) > available
        ):
            raise ValueError("runtime_declared_journal_budget_too_small")
        object.__setattr__(self, "max_event_bytes", envelope)
        if self.poll_interval_seconds > self.max_gap_seconds:
            raise ValueError("runtime_cadence_poll_exceeds_gap")
        if self.data_mode == "OBSERVED" and self.poll_interval_seconds > (
            self.simulation.max_age_seconds
        ):
            # Between polls the held mark would age out, hiding NAV and the loss controls.
            raise ValueError("runtime_cadence_exceeds_quote_freshness")
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
