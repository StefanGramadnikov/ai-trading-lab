"""Immutable V0 configuration: the user-approved DEC-043 numbers, paper only.

These are paper-experiment values. They are not live-trading limits, and changing any of them
requires a new recorded decision.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.paper_execution.models import PositiveAmount, SimulationPolicy


class RiskConfig(FrozenModel):
    position_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    exposure_fraction: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    daily_loss_fraction: Decimal = Field(default=Decimal("0.03"), gt=0, lt=1)
    drawdown_fraction: Decimal = Field(default=Decimal("0.10"), gt=0, lt=1)
    stop_loss: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    take_profit: Decimal = Field(default=Decimal("0.08"), gt=0, le=10)
    max_hold_hours: int = Field(default=72, ge=1, le=24 * 30, strict=True)
    cooldown_hours: int = Field(default=6, ge=0, le=24 * 7, strict=True)
    entry_interval_hours: int = Field(default=24, ge=0, le=24 * 7, strict=True)
    limit_buffer_bps: int = Field(default=10, ge=0, le=100, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> RiskConfig:
        if self.exposure_fraction < self.position_fraction:
            raise ValueError("exposure_must_cover_one_position")
        return self


class BreakoutConfig(FrozenModel):
    """Rules-only control strategy: breakout above the prior 48 h high, trend-break exit.

    The parameters are a design choice for the control strategy, not part of DEC-043.
    """

    version: str = "breakout-v1"
    interval_minutes: int = Field(default=15, ge=1, strict=True)
    entry_lookback: int = Field(default=192, ge=24, le=700, strict=True)  # 48 h of 15 min
    exit_lookback: int = Field(default=96, ge=12, le=700, strict=True)  # 24 h of 15 min
    min_volume_ratio: Decimal = Field(default=Decimal(1), gt=0, le=100)

    @model_validator(mode="after")
    def windows(self) -> BreakoutConfig:
        if self.exit_lookback > self.entry_lookback:
            raise ValueError("exit_window_cannot_exceed_entry_window")
        return self


class LiveConfig(FrozenModel):
    version: str = "v0-dec043"
    pairs: tuple[str, ...] = ("XBTEUR", "ETHEUR", "SOLEUR")
    cash_unit: str = "EUR"
    initial_cash: PositiveAmount = Decimal(100)
    fee_bps: Decimal = Field(default=Decimal(80), ge=0, le=1000)
    cycle_seconds: int = Field(default=900, ge=60, le=86400, strict=True)
    max_cost_bps: Decimal = Field(default=Decimal(250), gt=0, le=10000)
    risk: RiskConfig = RiskConfig()
    breakout: BreakoutConfig = BreakoutConfig()

    def policy(self) -> SimulationPolicy:
        """The paper broker's static ceilings for an actively traded portfolio."""
        return SimulationPolicy(
            version=self.version,
            fee_bps=self.fee_bps,
            latency_ms=1000,
            max_age_seconds=60,
            future_skew_seconds=5,
            max_order_notional=self.initial_cash * self.risk.position_fraction,
            max_position_notional=self.initial_cash * self.risk.position_fraction,
            max_total_exposure=self.initial_cash * self.risk.exposure_fraction,
            max_cost_bps=self.max_cost_bps,
        )

    def benchmark_policy(self) -> SimulationPolicy:
        """The passive buy-and-hold benchmark spends its whole capital once, split by pair."""
        return SimulationPolicy(
            version=self.version + "-benchmark",
            fee_bps=self.fee_bps,
            latency_ms=1000,
            max_age_seconds=60,
            future_skew_seconds=5,
            max_order_notional=self.initial_cash,
            max_position_notional=self.initial_cash,
            max_total_exposure=self.initial_cash,
            max_cost_bps=self.max_cost_bps,
        )
