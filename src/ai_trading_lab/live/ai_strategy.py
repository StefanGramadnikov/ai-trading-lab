"""The AI strategy's I/O: a numeric-only snapshot in, a direction (buy / sell / hold) out.

The model never sees text from the outside world, only numbers computed here from validated
evidence. Its answer is a direction and nothing else: size, price, limits, risk and every exit rule
stay deterministic (see `live/strategies.py`). Every call goes through the budget guard, which
reserves its worst-case cost before anything is sent and refuses once the global cap would be
crossed. A failed, refused, invalid or unaffordable call is a hold, never a trade.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, JsonValue

from ai_trading_lab.ai.budget import BudgetPolicy, BudgetStore, ProviderLimit
from ai_trading_lab.ai.models import (
    AIRequest,
    Evidence,
    FlowConfig,
    ModelAnswer,
    PriceSnapshot,
    ProviderResult,
    Role,
)
from ai_trading_lab.ai.orchestration import orchestrate
from ai_trading_lab.ai.providers import Provider
from ai_trading_lab.ai.service import InferenceService
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.live.strategies import Market, Proposal
from ai_trading_lab.market_data.adapters.kraken import Candle
from ai_trading_lab.market_data.models import FrozenModel

# 24 h of 15-minute candles plus the one it is compared with.
MIN_CANDLES = 97
# The newest closed candle must be recent: a model must not reason over a frozen history.
MAX_CANDLE_AGE = timedelta(minutes=45)
# A call reserved this long ago and never finished belongs to a process that is gone.
PENDING_AFTER = timedelta(minutes=10)
# The orchestration ends a flow early, with these reasons, when the model itself holds back.
ANSWERED = ("completed", "advisory_abstain", "advisory_veto")
# Reasons that mean the provider could not be reached or did not answer usably.
PROVIDER_OUTAGE_PREFIXES = (
    "settled_",
    "http_error",
    "timeout",
    "transport",
    "unsafe_",
    "destination_",
    "encoded_response",
    "response_too_large",
)
CANDLES_PER_HOUR = 4


class AIConfig(FrozenModel):
    """How the AI strategy asks. The binding money ceiling is the global cap in Settings."""

    provider: Literal["openai", "fixture"] = "openai"
    model: str = Field(default="gpt-6.1-sol", pattern=r"^[A-Za-z0-9_.:-]+$", max_length=200)
    review_interval_hours: int = Field(default=4, ge=1, le=24, strict=True)
    max_input_tokens: int = Field(default=6000, ge=1000, le=32768, strict=True)
    # Counts hidden reasoning tokens too: a reasoning model needs room beyond the ~150-token answer.
    max_output_tokens: int = Field(default=2000, ge=50, le=8192, strict=True)
    # Calls with an unknown outcome allowed per day before the AI pauses until the day passes.
    max_unknown_per_day: int = Field(default=5, ge=1, le=100, strict=True)
    timeout_seconds: int = Field(default=30, ge=1, le=60, strict=True)
    total_limit_usd: Decimal = Field(default=Decimal(15), gt=0, le=1000)
    daily_limit_usd: Decimal = Field(default=Decimal("1.5"), gt=0, le=1000)


@dataclass(frozen=True)
class AIDeps:
    """Everything the engine needs to ask the AI: the client, its reviewed price and the budget."""

    provider: Provider
    price: PriceSnapshot
    budget: BudgetStore
    config: AIConfig = AIConfig()


@dataclass(frozen=True)
class Held:
    """What the model is told about an open position (all derived by deterministic code)."""

    entry_price: Decimal
    mark_price: Decimal
    age_hours: Decimal


def _num(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _pct(now: Decimal, then: Decimal) -> str:
    return _num(((now / then - 1) * 100).quantize(Decimal("0.01")))


def build_snapshot(
    *,
    pair: str,
    now: datetime,
    market: Market,
    candles: list[Candle],
    held: Held | None,
    config: LiveConfig,
) -> Evidence | None:
    """Bounded, numeric-only evidence for one pair, or None without enough recent history."""
    history = sorted(candles, key=lambda c: c.open_time_utc)
    if len(history) < MIN_CANDLES or not market.quote.bids or not market.quote.asks:
        return None
    if now - history[-1].close_time_utc > MAX_CANDLE_AGE:
        return None
    last24 = history[-(MIN_CANDLES - 1) :]
    closes = [c.close for c in history]
    bid, ask = market.quote.bids[0].price, market.quote.asks[0].price
    body: dict[str, JsonValue] = {
        "label": "untrusted_data_only",
        "pair": pair,
        "bid": _num(bid),
        "ask": _num(ask),
        "spread_bps": _num(((ask - bid) / ((ask + bid) / 2) * 10000).quantize(Decimal("0.1"))),
        "last_close": _num(closes[-1]),
        "closes_15m_last_4h": [_num(c) for c in closes[-16:]],
        "closes_1h_last_24h": [_num(c) for c in closes[-1::-CANDLES_PER_HOUR][:24][::-1]],
        "high_24h": _num(max(c.high for c in last24)),
        "low_24h": _num(min(c.low for c in last24)),
        "volume_24h": _num(sum((c.volume for c in last24), Decimal(0))),
        "change_pct": {
            "1h": _pct(closes[-1], closes[-1 - CANDLES_PER_HOUR]),
            "4h": _pct(closes[-1], closes[-1 - 4 * CANDLES_PER_HOUR]),
            "24h": _pct(closes[-1], closes[-1 - 24 * CANDLES_PER_HOUR]),
        },
        "position": None
        if held is None
        else {
            "entry_price": _num(held.entry_price),
            "pnl_pct": _pct(held.mark_price, held.entry_price),
            "age_hours": _num(held.age_hours.quantize(Decimal("0.1"))),
        },
        "rules": {
            "long_only": True,
            "fee_pct_per_side": _num(config.fee_bps / 100),
            "stop_loss_pct": _num(config.risk.stop_loss * 100),
            "take_profit_pct": _num(config.risk.take_profit * 100),
            "max_hold_hours": config.risk.max_hold_hours,
        },
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return Evidence(
        instrument_key=market.quote.instrument.key,
        snapshot_id=uuid5(NAMESPACE_URL, f"ai-trading-lab/live/snapshot/{digest}"),
        available_at_utc=now,
        evidence_ids=(f"snapshot:{digest[:16]}",),
        payload=payload,
    )


def _code(exc: Exception) -> str:
    """The guard's own refusal codes are readable as they are; anything else is its type."""
    text = str(exc)
    return text if re.fullmatch(r"[a-z_]{1,60}", text) else type(exc).__name__.lower()[:40]


def _failure(code: str, detail: dict[str, JsonValue] | None = None) -> Proposal:
    # A failure that reached the provider path counts as a review (it may have cost money and is
    # retried at the next interval); one that stopped locally is retried at the next cycle.
    reached = detail is not None and "provider_status" in detail
    return Proposal(
        action="hold", reason=f"ai_unavailable_{code}"[:100], detail=detail, reviewed=reached
    )


class Proposer:
    """Asks the model about one pair at a time; every outcome becomes a `Proposal`."""

    def __init__(self, deps: AIDeps, session: str, journal: ErrorJournal | None = None) -> None:
        self.deps = deps
        self.session = session
        self.journal = journal
        # An unknown outcome is charged at its reserved maximum and the loop carries on.
        self.service = InferenceService(deps.budget, deps.provider, settle_unknown=True)
        cfg = deps.config
        # Derived from the limits, so a changed limit is a new envelope rather than a rewrite. The
        # global cap in Settings counts every envelope, so this never resets what was spent.
        tag = f"{cfg.total_limit_usd}/{cfg.daily_limit_usd}"
        self.budget_id = uuid5(NAMESPACE_URL, f"ai-trading-lab/live/{session}/budget/{tag}")
        self.run_id = uuid5(NAMESPACE_URL, f"ai-trading-lab/live/{session}/run")
        self.policy = BudgetPolicy(
            budget_id=self.budget_id,
            version=f"live-{session}-v1",
            total_limit=cfg.total_limit_usd,
            daily_limit=cfg.daily_limit_usd,
            monthly_limit=cfg.total_limit_usd,
            provider_limits=(ProviderLimit(provider=cfg.provider, limit=cfg.total_limit_usd),),
            max_calls=1000,
        )

    async def _note(self, exc: Exception, strategy_id: str, pair: str, now: datetime) -> None:
        if self.journal is not None:
            await self.journal.record_exception(
                "ai_strategy", exc, strategy_id=strategy_id, pair=pair, at=now
            )

    async def _note_provider(
        self, last: ProviderResult, strategy_id: str, pair: str, now: datetime
    ) -> None:
        """A provider that cannot be reached or answered is the signal behind an "AI is down" alert.

        Budget and policy refusals are not outages and are not recorded here.
        """
        if self.journal is None:
            return
        if last.reason.endswith("invalid_provider_response"):
            # The provider answered, but not usably: a quality problem, not "the AI is down".
            await self.journal.record_event(
                "ai_strategy",
                "invalid_provider_response",
                "The model reply did not validate.",
                strategy_id=strategy_id,
                pair=pair,
                headline="The AI reply could not be used",
                at=now,
            )
            return
        outage = last.status == "settled" or last.reason.startswith(PROVIDER_OUTAGE_PREFIXES)
        if outage:
            await self.journal.record_event(
                "ai_provider",
                last.reason[:100],
                f"status={last.status}",
                strategy_id=strategy_id,
                pair=pair,
                headline=f"AI provider problem: {last.reason}"[:200],
                at=now,
            )

    async def ensure_budget(self) -> None:
        await self.deps.budget.create(self.policy)

    async def _housekeeping(self, now: datetime) -> str | None:
        """Settle calls a crash left unfinished; pause the AI after repeated unknown outcomes."""
        budget = self.deps.budget
        await budget.settle_pending(self.budget_id, now - PENDING_AFTER)
        unknown = await budget.settled_since(self.budget_id, now - timedelta(days=1))
        if unknown >= self.deps.config.max_unknown_per_day:
            return "failure_breaker"
        return None

    def _flow(self) -> FlowConfig:
        cfg = self.deps.config
        return FlowConfig(
            mode="hybrid",
            roles=("strategy",),
            max_steps=1,
            max_rounds=1,
            # DNS check and the call each get the call timeout, so the flow must outlast both.
            timeout_seconds=min(120, 2 * cfg.timeout_seconds + 10),
            total_input_tokens=cfg.max_input_tokens,
            total_output_tokens=cfg.max_output_tokens,
        )

    async def propose(
        self,
        *,
        strategy_id: str,
        pair: str,
        market: Market,
        candles: list[Candle],
        held: Held | None,
        now: datetime,
        slot: int,
        live: LiveConfig,
    ) -> Proposal:
        cfg = self.deps.config
        call_id = uuid5(NAMESPACE_URL, f"ai-trading-lab/live/{strategy_id}/{pair}/{slot}")
        try:
            evidence = build_snapshot(
                pair=pair, now=now, market=market, candles=candles, held=held, config=live
            )
        except Exception as exc:  # e.g. a zero price in the history
            await self._note(exc, strategy_id, pair, now)
            return _failure(_code(exc))
        if evidence is None:
            return _failure("no_recent_candles")
        try:
            if (paused := await self._housekeeping(now)) is not None:
                return _failure(paused)
        except Exception as exc:  # the budget ledger must be readable before anything is paid
            await self._note(exc, strategy_id, pair, now)
            return _failure(_code(exc), {"call_id": str(call_id)})

        async def invoke(role: Role, prior: tuple[ModelAnswer, ...]) -> ProviderResult:
            request = AIRequest(
                call_id=call_id,
                provider=cfg.provider,
                model=cfg.model,
                role=role,
                evidence=evidence,
                prior=prior,
                max_input_tokens=cfg.max_input_tokens,
                max_output_tokens=cfg.max_output_tokens,
                timeout_seconds=cfg.timeout_seconds,
            )
            return await self.service.call(
                self.budget_id, self.run_id, cfg.total_limit_usd, request, self.deps.price, now
            )

        try:
            flow = await orchestrate(self._flow(), evidence, invoke)
        except Exception as exc:  # a broken AI path must never stop the mechanical exits
            await self._note(exc, strategy_id, pair, now)
            return _failure(_code(exc), {"call_id": str(call_id)})
        last = flow.trace[-1].result if flow.trace else None
        detail: dict[str, JsonValue] = {
            "call_id": str(call_id),
            "model": cfg.model,
            "flow": flow.reason,
            "evidence_ids": list(evidence.evidence_ids),
            "answer": {
                "action": flow.final.action,
                "limit_price": flow.final.limit_price,
                "reason": flow.final.reason,
            },
        }
        if last is not None:
            detail["provider_status"] = last.status
            detail["provider_reason"] = last.reason
            await self._note_provider(last, strategy_id, pair, now)
            if last.usage is not None:
                price = self.deps.price
                detail["usage"] = {
                    "input_tokens": last.usage.input_tokens,
                    "output_tokens": last.usage.output_tokens,
                }
                cost = (
                    last.usage.input_tokens * price.input_per_million
                    + last.usage.output_tokens * price.output_per_million
                ) / Decimal(1_000_000)
                detail["cost_usd"] = _num(cost)
        if flow.reason not in ANSWERED:
            reason = (
                last.reason
                if last is not None and flow.reason.startswith("provider_")
                else flow.reason
            )
            # The provider path was entered, so this is a review even if no outcome came back.
            return _failure(reason.replace(" ", "_")[:60], detail).model_copy(
                update={"reviewed": True}
            )
        action = flow.final.action
        if action in ("buy", "sell"):
            return Proposal(action=action, reason=f"ai_{action}", detail=detail)
        return Proposal(action="hold", reason=f"ai_{action}", detail=detail)


def held_info(
    *,
    cost_basis: Decimal,
    quantity: Decimal,
    mark_price: Decimal,
    entered_at: datetime,
    now: datetime,
) -> Held:
    return Held(
        entry_price=cost_basis / quantity,
        mark_price=mark_price,
        age_hours=Decimal(str((now - entered_at).total_seconds())) / Decimal(3600),
    )


__all__ = [
    "AIConfig",
    "AIDeps",
    "Held",
    "Proposer",
    "build_snapshot",
    "held_info",
]
