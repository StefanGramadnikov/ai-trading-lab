"""Deterministically rebuild all paper projections from immutable commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, field_validator

from ai_trading_lab.market_data.models import DataMode, FrozenModel
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.accounting import (
    JournalLine,
    PortfolioState,
    admit,
    apply_fill,
    open_portfolio,
    settle_position,
)
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    OrderIntent,
    PaperInstrument,
    SimulationPolicy,
    SimulationResult,
    decimal_amount,
)
from ai_trading_lab.paper_execution.replay import ReplayManifest
from ai_trading_lab.paper_execution.simulation import simulate


class OrderReceipt(FrozenModel):
    order_id: UUID
    intent: OrderIntent
    quote: ExecutionQuote
    submitted_at_utc: AwareDatetime
    result: SimulationResult
    state: PortfolioState
    duplicate: bool = False

    @field_validator("submitted_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def clock(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Aware UTC clock required")
    return at.astimezone(UTC)


def identity(instrument: PaperInstrument) -> str:
    return content_hash([instrument.source.value, instrument.venue, instrument.native_id])


def request_hash(kind: str, payload: dict[str, object]) -> str:
    if kind == "order":
        return OrderIntent.model_validate(payload["intent"]).fingerprint
    return content_hash(payload)


@dataclass
class Effect:
    command_id: UUID
    kind: Literal["order", "pause", "settle"]
    key: str
    submitted_at: datetime
    payload: dict[str, object]
    state: PortfolioState
    at: datetime
    lines: tuple[JournalLine, ...]
    events: list[tuple[str, dict[str, object]]]
    receipt: OrderReceipt | None = None


class History:
    def __init__(self, portfolio_id: UUID, config: dict[str, object]) -> None:
        self.policy = SimulationPolicy.model_validate(config["policy"])
        self.runtime_observed = (
            "runtime_session_id" in config and config.get("runtime_data_mode") == "OBSERVED"
        )
        self.replay_manifest = (
            ReplayManifest.model_validate(config["replay_manifest"])
            if "replay_manifest" in config
            else None
        )
        self.state, self.funding = open_portfolio(
            portfolio_id,
            str(config["cash_unit"]),
            decimal_amount(config["cash_step"]),
            decimal_amount(config["initial_cash"]),
        )
        self.effects: dict[UUID, Effect] = {}
        self.definitions: dict[str, tuple[PaperInstrument, bool]] = {}
        self.quotes: dict[
            UUID, tuple[ExecutionQuote, tuple[Decimal, ...], tuple[Decimal, ...]]
        ] = {}
        self.last_at: datetime | None = None

    def apply(
        self,
        kind: Literal["order", "pause", "settle"],
        key: str,
        payload: dict[str, object],
        at: datetime,
    ) -> Effect:
        at = clock(at)
        if not 1 <= len(key) <= 200:
            raise ValueError("Invalid command key")
        command_id = uuid5(self.state.portfolio_id, kind + ":" + key)
        if command_id in self.effects:
            raise ValueError("Duplicate authoritative command")
        if self.last_at is not None and at < self.last_at:
            raise ValueError("Paper clock cannot move backwards")
        initial_sequence = self.state.sequence
        lines: tuple[JournalLine, ...] = ()
        receipt: OrderReceipt | None = None
        end = at
        if kind == "order":
            intent = OrderIntent.model_validate(payload["intent"])
            quote = ExecutionQuote.model_validate(payload["quote"])
            if intent.portfolio_id != self.state.portfolio_id or intent.idempotency_key != key:
                raise ValueError("Order command identity mismatch")
            verified_observed = False
            if quote.data_mode == DataMode.OBSERVED and self.runtime_observed:
                from ai_trading_lab.paper_execution.replay import ReplayEntry
                from ai_trading_lab.runtime.evidence import observed_quote

                entry = ReplayEntry.model_validate(payload.get("source_entry"))
                if observed_quote(entry) != quote:
                    raise ValueError("runtime_raw_quote_mismatch")
                verified_observed = True
            result, lines, kinds = self._order(intent, quote, at, verified_observed)
            end = max(at, result.executed_at_utc)
            self.state = self.state.model_copy(update={"sequence": initial_sequence + len(kinds)})
            receipt = OrderReceipt(
                order_id=command_id,
                intent=intent,
                quote=quote,
                submitted_at_utc=at,
                result=result,
                state=self.state,
            )
        elif kind == "pause":
            if type(payload.get("paused")) is not bool:
                raise ValueError("Pause requires explicit boolean")
            self.state = self.state.model_copy(
                update={"paused": payload["paused"], "sequence": initial_sequence + 1}
            )
            kinds = [("PAUSE", payload)]
        else:
            instrument = PaperInstrument.model_validate(payload["instrument"])
            native = identity(instrument)
            definition = self.definitions.get(native)
            if definition is None or definition[0] != instrument or definition[1]:
                raise ValueError("Unknown, changed or already settled instrument")
            if type(payload.get("void")) is not bool:
                raise ValueError("Void requires explicit boolean")
            self.state, lines = settle_position(
                self.state,
                instrument,
                decimal_amount(payload["payout_per_unit"]),
                void=bool(payload["void"]),
            )
            self.state = self.state.model_copy(update={"sequence": initial_sequence + 1})
            self.definitions[native] = (instrument, True)
            kinds = [("SETTLE", payload)]
        effect = Effect(command_id, kind, key, at, payload, self.state, end, lines, kinds, receipt)
        self.effects[command_id] = effect
        self.last_at = end
        return effect

    def _order(
        self,
        intent: OrderIntent,
        quote: ExecutionQuote,
        at: datetime,
        verified_observed: bool = False,
    ) -> tuple[SimulationResult, tuple[JournalLine, ...], list[tuple[str, dict[str, object]]]]:
        instrument = intent.instrument
        native = identity(instrument)
        definition = self.definitions.get(native)
        reason = admit(self.state, intent, self.policy)
        if (
            quote.data_mode != DataMode.FIXTURE
            and not verified_observed
            and not (self.replay_manifest is not None and self.replay_manifest.allows(quote))
        ):
            reason = "fixture_mode_required"
        if definition is not None:
            if definition[0] != instrument:
                reason = "instrument_definition_conflict"
            elif definition[1]:
                reason = "instrument_settled"
        previous = self.quotes.get(quote.snapshot_id)
        consumed: tuple[Decimal, ...] = ()
        if previous is not None:
            if previous[0] != quote:
                reason = "snapshot_conflict"
            consumed = previous[1] if intent.side == "buy" else previous[2]
        result = (
            SimulationResult(
                status="REJECTED",
                reason=reason,
                quantity=Decimal(0),
                notional=Decimal(0),
                fee=Decimal(0),
                consumed=consumed,
                executed_at_utc=at,
            )
            if reason
            else simulate(intent, quote, self.policy, at, consumed)
        )
        if not result.quantity:
            return result, (), [(result.status, {"reason": result.reason})]
        with localcontext() as context:
            context.prec = 100
            reserve = intent.quantity
            if intent.side == "buy":
                notional = (
                    intent.quantity * intent.limit_price / self.state.cash_step
                ).to_integral_value(rounding=ROUND_CEILING) * self.state.cash_step
                reserve = (
                    notional
                    + (
                        notional * self.policy.fee_bps / Decimal(10000) / self.state.cash_step
                    ).to_integral_value(rounding=ROUND_CEILING)
                    * self.state.cash_step
                )
        self.state, lines = apply_fill(self.state, intent, result)
        self.definitions[native] = (instrument, False)
        buy, sell = (previous[1], previous[2]) if previous else ((), ())
        if intent.side == "buy":
            buy = result.consumed
        else:
            sell = result.consumed
        if instrument.kind == "spot":
            buy = sell = result.consumed
        self.quotes[quote.snapshot_id] = (quote, buy, sell)
        return (
            result,
            lines,
            [
                ("ACCEPTED", {}),
                (
                    "RESERVED",
                    {
                        "amount": str(reserve),
                        "unit": self.state.cash_unit
                        if intent.side == "buy"
                        else instrument.base_unit,
                    },
                ),
                (
                    "FILLED" if result.status == "FILLED" else "PARTIALLY_FILLED",
                    {"quantity": str(result.quantity)},
                ),
                ("RELEASED", {"remainder_cancelled": result.status != "FILLED"}),
            ],
        )
