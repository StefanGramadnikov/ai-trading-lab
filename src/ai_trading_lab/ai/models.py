"""Immutable provider-neutral contracts; model output has proposal authority only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.models import NonnegativeAmount, decimal_amount

ProviderName = Literal["fixture", "openai", "xai", "anthropic"]
Role = Literal["vet", "book", "research", "strategy", "risk"]


class Evidence(FrozenModel):
    instrument_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: UUID
    available_at_utc: AwareDatetime
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    # This is canonical JSON from validated local evidence, never instructions.
    payload: str = Field(min_length=1, max_length=16000)

    @field_validator("available_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ModelAnswer(FrozenModel):
    action: Literal["buy", "sell", "abstain", "veto"]
    instrument_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    limit_price: str | None = Field(max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def proposal(self) -> ModelAnswer:
        if self.action in ("buy", "sell"):
            if self.limit_price is None or decimal_amount(self.limit_price) <= 0:
                raise ValueError("positive_proposal_price_required")
        elif self.limit_price is not None:
            raise ValueError("abstention_cannot_set_price")
        return self


class AIRequest(FrozenModel):
    call_id: UUID
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")
    role: Role
    prompt_version: Literal["m5-v1"] = "m5-v1"
    evidence: Evidence
    prior: tuple[ModelAnswer, ...] = Field(default=(), max_length=20)
    max_input_tokens: int = Field(ge=1, le=32768, strict=True)
    max_output_tokens: int = Field(ge=1, le=8192, strict=True)
    timeout_seconds: int = Field(ge=1, le=60, strict=True)

    @property
    def fingerprint(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class Usage(FrozenModel):
    input_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    output_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    cached_input_tokens: int = Field(default=0, ge=0, le=1_000_000, strict=True)

    @model_validator(mode="after")
    def cached(self) -> Usage:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_tokens_exceed_input")
        return self


class ProviderResult(FrozenModel):
    status: Literal["completed", "refused", "invalid", "timeout", "error", "unknown"]
    answer: ModelAnswer | None = None
    usage: Usage | None = None
    response_id: str | None = Field(default=None, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=200)
    response_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    elapsed_ms: int | None = Field(default=None, ge=0, le=300000, strict=True)
    completed_at_utc: AwareDatetime | None = None

    @field_validator("completed_at_utc")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def completed(self) -> ProviderResult:
        if self.status == "completed" and (self.answer is None or self.usage is None):
            raise ValueError("completed_requires_answer_and_usage")
        if self.status != "completed" and self.answer is not None:
            raise ValueError("failed_call_cannot_propose")
        return self


class PriceSnapshot(FrozenModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    currency: Literal["USD"] = "USD"
    input_per_million: NonnegativeAmount
    output_per_million: NonnegativeAmount
    source_url: str = Field(min_length=1, max_length=500)
    verified_at_utc: AwareDatetime
    version: str = Field(min_length=1, max_length=200)

    @field_validator("verified_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class FlowConfig(FrozenModel):
    mode: Literal["hybrid", "sequential", "collaborative"]
    roles: tuple[Role, ...] = Field(min_length=1, max_length=5)
    max_steps: int = Field(ge=1, le=20, strict=True)
    max_rounds: int = Field(ge=1, le=3, strict=True)
    timeout_seconds: int = Field(ge=1, le=120, strict=True)
    total_input_tokens: int = Field(ge=1, le=131072, strict=True)
    total_output_tokens: int = Field(ge=1, le=32768, strict=True)

    @model_validator(mode="after")
    def role_order(self) -> FlowConfig:
        if len(set(self.roles)) != len(self.roles) or "strategy" not in self.roles:
            raise ValueError("unique_roles_with_strategy_required")
        if self.mode == "hybrid" and self.roles != ("strategy",):
            raise ValueError("hybrid_requires_one_strategy_role")
        if "risk" in self.roles and self.roles[-1] != "risk":
            raise ValueError("risk_critique_must_be_last")
        if self.roles[-1] not in ("strategy", "risk"):
            raise ValueError("strategy_or_risk_must_finish_flow")
        if self.mode != "collaborative" and self.max_rounds != 1:
            raise ValueError("only_collaborative_has_multiple_rounds")
        return self


class Trace(FrozenModel):
    role: Role
    round: int = Field(ge=0)
    result: ProviderResult


class FlowResult(FrozenModel):
    final: ModelAnswer
    trace: tuple[Trace, ...]
    reason: str
