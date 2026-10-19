"""Content-bound research inputs and decision audit contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.market_data.models import DataMode, FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.accounting import PortfolioState
from ai_trading_lab.paper_execution.history import OrderReceipt
from ai_trading_lab.paper_execution.models import PositiveAmount, SimulationPolicy
from ai_trading_lab.paper_execution.replay import ReplayManifest
from ai_trading_lab.strategies.models import Abstain, StrategyConfig, StrategyFrame, TradeProposal


def runtime_fingerprint() -> str:
    """Bind reports to installed Python sources, including uncommitted changes."""
    root = Path(__file__).resolve().parents[1]
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class ResearchInput(FrozenModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID
    code_version: str = Field(pattern=r"^[0-9a-f]{40}$")
    strategy: StrategyConfig
    simulation: SimulationPolicy
    cash_unit: str = Field(min_length=1, max_length=200)
    cash_step: PositiveAmount
    initial_cash: PositiveAmount
    order_notional: PositiveAmount
    order_ttl_seconds: int = Field(ge=1, le=86400, strict=True)
    start_at_utc: AwareDatetime
    end_at_utc: AwareDatetime
    data_mode: Literal[DataMode.FIXTURE, DataMode.REPLAY]
    frames: tuple[StrategyFrame, ...] = Field(min_length=1, max_length=100)
    replay_manifest: ReplayManifest | None = None

    @field_validator("start_at_utc", "end_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def tape(self) -> ResearchInput:
        if self.end_at_utc < self.start_at_utc:
            raise ValueError("invalid_research_window")
        previous = self.start_at_utc
        snapshots: dict[UUID, object] = {}
        latency = timedelta(milliseconds=self.simulation.latency_ms)
        for frame in self.frames:
            if not previous <= frame.at_utc <= self.end_at_utc:
                raise ValueError("tape_must_be_chronological_inside_window")
            previous = frame.at_utc + latency
            quote = frame.quote
            if quote.data_mode != self.data_mode:
                raise ValueError("mixed_or_observed_execution_mode")
            old = snapshots.setdefault(quote.snapshot_id, quote)
            if old != quote:
                raise ValueError("snapshot_definition_conflict")
            if self.data_mode == DataMode.REPLAY:
                if self.replay_manifest is None or not self.replay_manifest.allows(quote):
                    raise ValueError("unqualified_replay_quote")
                entry = next(e for e in self.replay_manifest.entries if e.quote == quote)
                if frame.resolution_terms_hash != entry.resolution_terms_hash:
                    raise ValueError("replay_resolution_terms_mismatch")
        if previous > self.end_at_utc:
            raise ValueError("execution_exceeds_research_window")
        if self.data_mode == DataMode.FIXTURE and self.replay_manifest is not None:
            raise ValueError("fixture_cannot_register_observed_evidence")
        return self

    @property
    def fingerprint(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    @property
    def dataset_hash(self) -> str:
        return content_hash(
            {
                "frames": [frame.model_dump(mode="json") for frame in self.frames],
                "mode": self.data_mode.value,
                "manifest": self.replay_manifest.model_dump(mode="json")
                if self.replay_manifest is not None
                else None,
                "start": self.start_at_utc.isoformat(),
                "end": self.end_at_utc.isoformat(),
            }
        )


class DecisionRecord(FrozenModel):
    index: int = Field(ge=0)
    at_utc: AwareDatetime
    snapshot_id: UUID
    decision: Annotated[TradeProposal | Abstain, Field(discriminator="kind")]
    sizing_reason: str | None = None
    receipt: OrderReceipt | None = None


class Evaluation(FrozenModel):
    run_id: UUID
    input_hash: str
    dataset_hash: str
    engine_hash: str = Field(default_factory=runtime_fingerprint, pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[DecisionRecord, ...]
    final_state: PortfolioState
