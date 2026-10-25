"""Reserve before inference, journal outcomes, and never blindly retry."""

from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic_ns
from uuid import UUID

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.ai.models import AIRequest, PriceSnapshot, ProviderResult
from ai_trading_lab.ai.providers import Provider


class InferenceService:
    def __init__(self, store: BudgetStore, provider: Provider) -> None:
        self.store, self.provider = store, provider

    async def call(
        self,
        budget_id: UUID,
        run_id: UUID,
        run_limit: Decimal,
        request: AIRequest,
        price: PriceSnapshot,
        at: datetime,
    ) -> ProviderResult:
        # Requests the adapter can already prove unsendable never reserve budget. A reserved
        # call with no response would otherwise be indistinguishable from an unknown outcome
        # and would block the whole envelope.
        preflight = getattr(self.provider, "preflight", None)
        if preflight is not None:
            rejected: ProviderResult | None = preflight(request)
            if rejected is not None:
                return rejected
        reservation = await self.store.reserve(
            budget_id, request, price, at=at, run_id=run_id, run_limit=run_limit
        )
        if reservation.status == "completed":
            assert reservation.result is not None
            return reservation.result
        if reservation.status != "new":
            return ProviderResult(status="error", model=request.model, reason=reservation.reason)
        started = monotonic_ns()
        try:
            result = await self.provider.analyze(request)
        except (TimeoutError, OSError, ValueError):
            result = ProviderResult(
                status="unknown", model=request.model, reason="provider_outcome_unknown"
            )
        result = ProviderResult.model_validate(result.model_dump(mode="json"))
        result = result.model_copy(
            update={
                "elapsed_ms": min(300000, (monotonic_ns() - started) // 1_000_000),
                "completed_at_utc": datetime.now(UTC),
            }
        )
        # Cancellation/crash before finish intentionally leaves a durable pending
        # reservation. Its existence prevents another paid request on restart.
        return await self.store.finish(budget_id, request.call_id, request.fingerprint, result)
