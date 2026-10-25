"""One live cycle: collect real Kraken data, decide, plan, submit, finish and record.

The loop is restart-safe by construction. An order decision is written as ``planned`` with its
intent and raw evidence before the paper order is submitted, so a crash anywhere is recovered at
the start of the next cycle by resubmitting the same idempotency key, which the broker answers
without a second fill. Strategy state is derived from fills, never guessed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.risk import RiskState, update_risk
from ai_trading_lab.live.store import LiveStore, LoopState, PlannedDecision, StrategyRow
from ai_trading_lab.live.strategies import (
    Decision,
    Mark,
    Market,
    decide_benchmark,
    decide_rules,
    held_position,
    liquidation,
)
from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter, parse_candles
from ai_trading_lab.market_data.models import Batch, DataQuality, FrozenModel, Source, Transport
from ai_trading_lab.market_data.storage import ObservationStore
from ai_trading_lab.paper_execution.history import identity
from ai_trading_lab.paper_execution.kraken_evidence import KrakenEntry
from ai_trading_lab.paper_execution.models import OrderIntent, SimulationResult
from ai_trading_lab.paper_execution.store import PaperStore

TransportFactory = Callable[[], AbstractAsyncContextManager[Transport]]
ORDER_SPACING = timedelta(
    seconds=2
)  # the paper ledger clock cannot move back, and includes latency


def _utcnow() -> datetime:
    return datetime.now(UTC)


class StrategyReport(FrozenModel):
    equity: Decimal | None
    orders: int = 0
    filled: int = 0
    rejected: int = 0
    holds: int = 0
    blocked: str | None = None


class CycleReport(FrozenModel):
    cycle_at: AwareDatetime
    observed: list[str]
    failures: list[str]
    strategies: dict[str, StrategyReport]


def build_markets(batch: Batch, config: LiveConfig) -> dict[str, Market]:
    """Per pair, the originals behind its book and the quote derived from them.

    A pair whose evidence does not hold together is left out, so strategies see it as having no
    market data rather than trading on something unverified.
    """
    captures = {c.capture_id: c for c in batch.captures}
    markets: dict[str, Market] = {}
    for observation in batch.observations:
        pair = observation.instrument.metadata.get("altname")
        if not isinstance(pair, str) or pair not in config.pairs:
            continue
        try:
            entry = KrakenEntry(
                pairs_capture=captures[_capture_id(observation.facts["pairs_capture_id"])],
                time_capture=captures[_capture_id(observation.facts["time_capture_id"])],
                book_capture=captures[observation.capture_id],
                pair=pair,
                assumption_version=config.version,
            )
            quote = entry.quote
        except (KeyError, ValueError):
            continue
        if quote.instrument.cash_unit != config.cash_unit:
            continue
        markets[pair] = Market(pair=pair, entry=entry, quote=quote, rules=entry.rules)
    return markets


def _capture_id(value: object) -> UUID:
    return UUID(str(value))


def _apply(
    loop: LoopState,
    decision: PlannedDecision,
    result: SimulationResult,
    after: Any,
    instrument: Any,
) -> LoopState:
    """Fold a fill into the loop state. Idempotent: it depends only on the fill itself."""
    if result.quantity <= 0:
        return loop
    at, pair = result.executed_at_utc, decision.pair
    if decision.action == "buy":
        entered = dict(loop.entered_at)
        entered.setdefault(pair, at)
        return loop.model_copy(
            update={"entered_at": entered, "last_entry_at": {**loop.last_entry_at, pair: at}}
        )
    if held_position(after, instrument) is not None:
        return loop  # a partial exit: keep the original entry time and try again next cycle
    update: dict[str, Any] = {"entered_at": {k: v for k, v in loop.entered_at.items() if k != pair}}
    if decision.reason == "stop_loss":
        update["last_stop_at"] = {**loop.last_stop_at, pair: at}
    return loop.model_copy(update=update)


def _current_block(risk: RiskState, today: Any) -> str | None:
    if risk.drawdown_latched:
        return "drawdown_latched"
    if risk.daily_suspended and risk.day == today:
        return "daily_loss_suspended"
    return None


class LiveEngine:
    def __init__(
        self,
        engine: AsyncEngine,
        transport: TransportFactory,
        config: LiveConfig,
        *,
        clock: Callable[[], datetime] = _utcnow,
        session: str = "v0",
    ) -> None:
        self.engine, self.transport, self.config = engine, transport, config
        self.clock, self.session = clock, session
        self.store, self.paper = LiveStore(engine), PaperStore(engine)
        self.observations = ObservationStore(engine)

    @property
    def rules_id(self) -> str:
        return f"{self.session}-rules-breakout"

    @property
    def benchmark_id(self) -> str:
        return f"{self.session}-benchmark"

    # ---- setup ------------------------------------------------------------------------

    async def setup(self) -> None:
        """Create the strategies' portfolios and registry rows; safe to repeat, never mutates."""
        now = self.clock()
        for strategy_id, kind, policy in (
            (self.rules_id, "rules", self.config.policy()),
            (self.benchmark_id, "benchmark", self.config.benchmark_policy()),
        ):
            portfolio_id = uuid5(NAMESPACE_URL, f"ai-trading-lab/live/{strategy_id}")
            await self.paper.create(
                portfolio_id,
                self.config.cash_unit,
                self.config.cash_step,
                self.config.initial_cash,
                policy,
                markers={"live_session_id": strategy_id, "live_data_mode": "OBSERVED"},
            )
            await self.store.register(
                strategy_id,
                kind,
                portfolio_id,
                {
                    "live": self.config.model_dump(mode="json"),
                    "policy": policy.model_dump(mode="json"),
                },
                now,
            )

    # ---- the cycle --------------------------------------------------------------------

    async def run_cycle(self) -> CycleReport:
        now = self.clock()
        batch = await self._collect()
        markets = build_markets(batch, self.config)
        failures = [f"{f.target}:{f.code}" for f in batch.failures]
        failures += [
            f"{p}:evidence_invalid"
            for p in self.config.pairs
            if p not in markets
            and any(o.instrument.metadata.get("altname") == p for o in batch.observations)
        ]
        reports: dict[str, StrategyReport] = {}
        for strategy in await self.store.strategies():
            await self._recover(strategy, now)
            reports[strategy.strategy_id] = await self._step(strategy, markets, now)
        return CycleReport(
            cycle_at=now, observed=sorted(markets), failures=failures, strategies=reports
        )

    async def _collect(self) -> Batch:
        interval = self.config.breakout.interval_minutes
        lasts = [await self.store.last_open_time(pair, interval) for pair in self.config.pairs]
        since = None
        if all(last is not None for last in lasts):
            oldest = min(last for last in lasts if last is not None)
            since = max(1, int(oldest.timestamp()) - interval * 60)
        async with self.transport() as transport:
            batch = await KrakenAdapter(transport).collect(
                self.config.pairs, candle_interval_minutes=interval, candles_since=since
            )
        await self.observations.save(Source.KRAKEN, batch)
        await self._store_candles(batch)
        return batch

    async def _store_candles(self, batch: Batch) -> None:
        by_pair = {
            o.instrument.metadata.get("altname"): o
            for o in batch.observations
            if o.event_time_utc is not None
        }
        for capture in batch.captures:
            if not capture.path.endswith("/OHLC"):
                continue
            pair = capture.params.get("pair", "")
            observation = by_pair.get(pair)
            if observation is None or observation.event_time_utc is None:
                continue
            try:
                candles = parse_candles(
                    capture,
                    server_time_utc=observation.event_time_utc,
                    expected_key=observation.instrument.native_id,
                )
            except ValueError:
                continue
            await self.store.upsert_candles(pair, list(candles))

    # ---- one strategy -----------------------------------------------------------------

    def _new_loop(self, now: datetime) -> LoopState:
        initial = self.config.initial_cash
        return LoopState(
            risk=RiskState(peak_equity=initial, day=now.date(), day_open_equity=initial)
        )

    async def _recover(self, strategy: StrategyRow, now: datetime) -> None:
        planned = await self.store.planned(strategy.strategy_id)
        if not planned:
            return
        loop = await self.store.load_state(strategy.strategy_id) or self._new_loop(now)
        for item in planned:
            assert item.intent is not None and item.evidence is not None
            try:
                intent = OrderIntent.model_validate(item.intent)
                entry = KrakenEntry.model_validate(item.evidence)
            except ValueError:
                await self.store.finish_decision(
                    item.decision_id, "rejected", {"error": "unreadable_planned_decision"}, now
                )
                continue
            loop, _ = await self._submit(strategy, item, intent, entry, now, loop)

    async def _step(
        self, strategy: StrategyRow, markets: Mapping[str, Market], now: datetime
    ) -> StrategyReport:
        sid = strategy.strategy_id
        state = await self.paper.get(strategy.portfolio_id)
        loop = await self.store.load_state(sid) or self._new_loop(now)
        policy = (
            self.config.policy() if strategy.kind == "rules" else self.config.benchmark_policy()
        )
        equity, marks = self._value(state, markets, policy, now)
        blocked: str | None = None
        if strategy.kind == "rules":
            if equity is not None:
                risk, blocked = update_risk(
                    loop.risk, equity, now, self.config.initial_cash, self.config.risk
                )
                loop = loop.model_copy(update={"risk": risk})
            else:
                blocked = _current_block(loop.risk, now.date())
            candles = {
                p: await self.store.candles(
                    p,
                    self.config.breakout.interval_minutes,
                    self.config.breakout.entry_lookback + 1,
                )
                for p in self.config.pairs
            }
            decisions = decide_rules(
                config=self.config,
                now=now,
                markets=markets,
                candles=candles,
                state=state,
                loop=loop,
                marks=marks,
                equity=equity,
                blocked=blocked,
            )
        else:
            decisions = decide_benchmark(
                config=self.config, now=now, markets=markets, state=state, loop=loop
            )
        report = {"orders": 0, "filled": 0, "rejected": 0, "holds": 0}
        submitted = 0
        for decision in decisions:
            if decision.order is None:
                await self.store.log_decision(
                    sid, decision.pair, now, decision.action, decision.reason
                )
                report["holds"] += 1
                continue
            at = now + submitted * ORDER_SPACING
            submitted += 1
            loop, status = await self._execute(strategy, decision, markets[decision.pair], at, loop)
            report["orders"] += 1
            report["filled" if status in ("filled", "partial") else "rejected"] += 1
        await self.store.save_state(sid, loop, now)
        if report["orders"]:
            # Record what was actually paid: equity after this cycle's fills, fees and spread.
            state = await self.paper.get(strategy.portfolio_id)
            equity, _ = self._value(state, markets, policy, now)
        if equity is not None:
            await self.store.record_equity(sid, now, equity, state.cash, equity - state.cash)
        return StrategyReport(equity=equity, blocked=blocked, **report)

    @staticmethod
    def _value(
        state: Any, markets: Mapping[str, Market], policy: Any, now: datetime
    ) -> tuple[Decimal | None, dict[str, Mark | None]]:
        """Equity = cash plus every position liquidated against its current book, or None."""
        by_identity = {identity(m.quote.instrument): m for m in markets.values()}
        marks: dict[str, Mark | None] = {}
        total = state.cash
        for position in state.positions:
            market = by_identity.get(identity(position.instrument))
            mark = liquidation(position, market.quote, policy, now) if market else None
            if market is not None:
                marks[market.pair] = mark
            if mark is None:
                return None, marks
            total += mark.net
        return total, marks

    # ---- orders -----------------------------------------------------------------------

    async def _execute(
        self,
        strategy: StrategyRow,
        decision: Decision,
        market: Market,
        at: datetime,
        loop: LoopState,
    ) -> tuple[LoopState, str]:
        order = decision.order
        assert order is not None
        slot = int(at.timestamp() // self.config.cycle_seconds)
        key = f"{strategy.strategy_id}:{decision.pair}:{decision.action}:{slot}"
        intent = OrderIntent(
            portfolio_id=strategy.portfolio_id,
            idempotency_key=key,
            instrument=market.quote.instrument,
            side=order.side,
            quantity=order.quantity,
            limit_price=order.limit_price,
            created_at_utc=at,
            expires_at_utc=at + timedelta(seconds=60),
        )
        planned = await self.store.plan_decision(
            strategy.strategy_id,
            decision.pair,
            at,
            decision.action,
            decision.reason,
            key,
            intent.model_dump(mode="json"),
            market.entry.model_dump(mode="json"),
        )
        if planned.status != "planned":
            return loop, planned.status  # this slot's order was already handled before a restart
        return await self._submit(strategy, planned, intent, market.entry, at, loop)

    async def _submit(
        self,
        strategy: StrategyRow,
        planned: PlannedDecision,
        intent: OrderIntent,
        entry: KrakenEntry,
        at: datetime,
        loop: LoopState,
    ) -> tuple[LoopState, str]:
        try:
            receipt = await self.paper.submit(intent, entry.quote, at, source_entry=entry)
        except ValueError as exc:
            await self.store.finish_decision(
                planned.decision_id, "rejected", {"error": str(exc)[:200]}, at
            )
            return loop, "rejected"
        result = receipt.result
        status = {"FILLED": "filled", "PARTIALLY_FILLED": "partial"}.get(result.status, "rejected")
        loop = _apply(loop, planned, result, receipt.state, intent.instrument)
        await self.store.save_state(strategy.strategy_id, loop, at)
        await self.store.finish_decision(
            planned.decision_id,
            status,  # type: ignore[arg-type]
            {
                "status": result.status,
                "reason": result.reason,
                "quantity": str(result.quantity),
                "notional": str(result.notional),
                "fee": str(result.fee),
                "executed_at": result.executed_at_utc.isoformat(),
                "duplicate": receipt.duplicate,
            },
            at,
        )
        return loop, status


__all__ = ["CycleReport", "LiveEngine", "StrategyReport", "build_markets"]
_ = DataQuality
