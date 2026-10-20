"""Explicit AI research configuration, distinct from provider activation."""

from typing import Literal

from pydantic import Field, model_validator

from ai_trading_lab.ai.budget import BudgetPolicy
from ai_trading_lab.ai.models import FlowConfig, PriceSnapshot, ProviderName, ProviderResult
from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.market_data.models import DataMode, FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.models import NonnegativeAmount


class AIRunInput(FrozenModel):
    schema_version: Literal["1"] = "1"
    research: ResearchInput
    inference_mode: Literal["fixture", "real"]
    purpose: Literal["development", "integration_smoke"]
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    price: PriceSnapshot
    budget: BudgetPolicy
    run_spend_limit: NonnegativeAmount
    flow: FlowConfig
    activation: Literal["periodic", "event"]
    interval_seconds: int = Field(ge=1, le=86400, strict=True)
    max_input_tokens: int = Field(ge=1, le=32768, strict=True)
    max_output_tokens: int = Field(ge=1, le=8192, strict=True)
    request_timeout_seconds: int = Field(ge=1, le=60, strict=True)
    fixture_results: tuple[ProviderResult, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def identity(self) -> "AIRunInput":
        if (self.price.provider, self.price.model) != (self.provider, self.model):
            raise ValueError("provider_price_identity_mismatch")
        if self.inference_mode == "fixture":
            if self.provider != "fixture" or not self.fixture_results:
                raise ValueError("fixture_provider_and_results_required")
        elif (
            self.provider == "fixture"
            or self.fixture_results
            or self.purpose != "integration_smoke"
            or self.research.data_mode != DataMode.FIXTURE
        ):
            # Fresh model knowledge cannot be presented as contemporaneous
            # historical forecasts. Real M5 activation is an integration smoke.
            raise ValueError("real_calls_require_explicit_synthetic_integration_smoke")
        if self.run_spend_limit > self.budget.total_limit:
            raise ValueError("run_limit_exceeds_global_limit")
        return self

    @property
    def fingerprint(self) -> str:
        return content_hash(self.model_dump(mode="json"))
