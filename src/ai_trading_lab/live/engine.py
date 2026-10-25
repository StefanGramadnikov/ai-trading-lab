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

from ai_trading_lab.live.ai_strategy import AIDeps, Proposer, held_info
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.errors import LoopIntegrityError
from ai_trading_lab.live.risk import RiskState, update_risk
from ai_trading_lab.live.store import LiveStore, LoopState, PlannedDecision, StrategyRow
from ai_trading_lab.live.strategies import (
    Decision,
    Mark,
    Market,
    Proposal,
    decide_ai,
    decide_benchmark,
    decide_rules,
    held_position,
    liquidation,
    review_candidates,
)
from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter, parse_candles
from ai_trading_lab.market_data.models import Batch, DataQuality, FrozenModel, Source, Transport
from ai_trading_lab.market_data.storage import ObservationStore
from ai_trading_lab.paper_execution.accounting import admit
from ai_trading_lab.paper_execution.history import identity
from ai_trading_lab.paper_execution.kraken_evidence import KrakenEntry
from ai_trading_lab.paper_execution.models import OrderIntent, SimulationResult
from ai_trading_lab.paper_execution.simulation import simulate
from ai_trading_lab.paper_execution.store import PaperStore

TransportFactory = Callable[[], AbstractAsyncContextManager[Transport]]
# The paper ledger clock cannot move back and includes the simulated latency.
ORDER_SPACING = timedelta(seconds=2)
# Every submitted order, even a refused one, is a permanent ledger command and the history is capped
# at 1000. New entries stop well before that; exits never do.
COMMAND_SOFT_LIMIT = 900
# ValueErrors from the ledger that mean corruption, a second writer or a broken invariant. They are
# never recorded as ordinary rejections or retried.
INTEGRITY_MARKERS = (
    "Idempotency key conflict",
    "Paper history exceeds",
    "Paper clock cannot move backwards",
    "integrity",
    "immutable",
    "live_raw_quote_mismatch",
    "live_source_evidence_required",
)


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
    errors: list[str] = []


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
        ai: AIDeps | None = None,
    ) -> None:
        self.engine, self.transport, self.config = engine, transport, config
        self.clock, self.session = clock, session
        # Without AI dependencies the AI strategy does not exist: nothing is asked or paid for.
        self.proposer = Proposer(ai, session) if ai is not None else None
        self.store, self.paper = LiveStore(engine), PaperStore(engine)
        self.observations = ObservationStore(engine)

    @property
    def rules_id(self) -> str:
        return f"{self.session}-rules-breakout"

    @property
    def benchmark_id(self) -> str:
        return f"{self.session}-benchmark"

    @property
    def ai_id(self) -> str:
        return f"{self.session}-ai"

    @property
    def strategy_ids(self) -> set[str]:
        ids = {self.rules_id, self.benchmark_id}
        return ids | {self.ai_id} if self.proposer is not None else ids

    # ---- setup ------------------------------------------------------------------------

    async def setup(self) -> None:
        """Create the strategies' portfolios and registry rows; safe to repeat, never mutates."""
        now = self.clock()
        plan = [
            (self.rules_id, "rules", self.config.policy()),
            (self.benchmark_id, "benchmark", self.config.benchmark_policy()),
        ]
        extra: dict[str, Any] = {}
        if self.proposer is not None:
            await self.proposer.ensure_budget()
            plan.append((self.ai_id, "ai", self.config.policy()))
            extra[self.ai_id] = {"ai": self.proposer.deps.config.model_dump(mode="json")}
        for strategy_id, kind, policy in plan:
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
                    **extra.get(strategy_id, {}),
                },
                now,
            )

    # ---- the cycle --------------------------------------------------------------------

    async def run_cycle(self) -> CycleReport:
        batch = await self._collect()
        # Decide only after the evidence exists: the cycle time can never precede a capture it uses,
        # or the broker would (rightly) treat that quote as not yet available.
        now = max([self.clock(), *(c.received_at_utc for c in batch.captures)])
        markets = build_markets(batch, self.config)
        failures = [f"{f.target}:{f.code}" for f in batch.failures]
        failures += [
            f"{p}:evidence_invalid"
            for p in self.config.pairs
            if p not in markets
            and any(o.instrument.metadata.get("altname") == p for o in batch.observations)
        ]
        reports: dict[str, StrategyReport] = {}
        errors: list[str] = []
        # The strategy that owns the mechanical exits runs first.
        own = self.strategy_ids
        strategies = sorted(
            (s for s in await self.store.strategies() if s.strategy_id in own),
            key=lambda s: (s.kind != "rules", s.strategy_id),
        )
        for strategy in strategies:
            try:
                await self._recover(strategy, now)
                reports[strategy.strategy_id] = await self._step(strategy, markets, now)
            except LoopIntegrityError:
                raise
            except Exception as exc:  # one strategy's bug must not starve the other
                errors.append(f"{strategy.strategy_id}:{type(exc).__name__}")
                reports[strategy.strategy_id] = StrategyReport(
                    equity=None, blocked="strategy_error"
                )
        return CycleReport(
            cycle_at=now,
            observed=sorted(markets),
            failures=failures,
            strategies=reports,
            errors=errors,
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
            stamped = await self._stamp(strategy, now)
            loop, _ = await self._submit(strategy, item, intent, entry, stamped, loop)

    async def _step(
        self, strategy: StrategyRow, markets: Mapping[str, Market], now: datetime
    ) -> StrategyReport:
        sid = strategy.strategy_id
        state = await self.paper.get(strategy.portfolio_id)
        loop = await self.store.load_state(sid) or self._new_loop(now)
        policy = (
            self.config.benchmark_policy() if strategy.kind == "benchmark" else self.config.policy()
        )
        equity, marks = self._value(state, markets, policy, now)
        blocked: str | None = None
        capacity = (
            "command_capacity"
            if await self.paper.command_count(strategy.portfolio_id) >= COMMAND_SOFT_LIMIT
            else None
        )
        if strategy.kind in ("rules", "ai"):
            if equity is not None:
                risk, blocked = update_risk(
                    loop.risk, equity, now, self.config.initial_cash, self.config.risk
                )
                loop = loop.model_copy(update={"risk": risk})
            else:
                blocked = _current_block(loop.risk, now.date())
            blocked = blocked or capacity
            candles = {
                p: await self.store.candles(
                    p,
                    self.config.breakout.interval_minutes,
                    self.config.breakout.entry_lookback + 1,
                )
                for p in self.config.pairs
            }
            if strategy.kind == "ai":
                proposals, loop = await self._review(
                    strategy, markets, candles, state, loop, marks, equity, blocked, now
                )
                decisions = decide_ai(
                    config=self.config,
                    now=now,
                    markets=markets,
                    state=state,
                    loop=loop,
                    marks=marks,
                    equity=equity,
                    blocked=blocked,
                    proposals=proposals,
                )
            else:
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
            blocked = capacity
            decisions = decide_benchmark(
                config=self.config,
                now=now,
                markets=markets,
                state=state,
                loop=loop,
                blocked=blocked,
            )
        report = {"orders": 0, "filled": 0, "rejected": 0, "holds": 0}
        submitted = 0
        for decision in decisions:
            if decision.order is None:
                await self.store.log_decision(
                    sid, decision.pair, now, decision.action, decision.reason, decision.detail
                )
                report["holds"] += 1
                continue
            at = now + submitted * ORDER_SPACING
            loop, status = await self._execute(
                strategy, decision, markets[decision.pair], at, loop, policy
            )
            if status == "skipped":
                report["holds"] += 1  # refused before submission: no ledger command was spent
                continue
            submitted += 1
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

    async def _review(
        self,
        strategy: StrategyRow,
        markets: Mapping[str, Market],
        candles: Mapping[str, Any],
        state: Any,
        loop: LoopState,
        marks: Mapping[str, Mark | None],
        equity: Decimal | None,
        blocked: str | None,
        now: datetime,
    ) -> tuple[dict[str, Proposal], LoopState]:
        """Ask the AI about each pair that is due and could change what the rules would do."""
        assert self.proposer is not None
        interval = timedelta(hours=self.proposer.deps.config.review_interval_hours)
        due = review_candidates(
            config=self.config,
            now=now,
            interval=interval,
            markets=markets,
            state=state,
            loop=loop,
            marks=marks,
            equity=equity,
            blocked=blocked,
        )
        slot = int(now.timestamp() // self.config.cycle_seconds)
        proposals: dict[str, Proposal] = {}
        for pair in due:
            market = markets[pair]
            position = held_position(state, market.quote.instrument)
            held = None
            if position is not None:
                mark = marks.get(pair)
                price = mark.price if mark is not None else market.quote.bids[0].price
                held = held_info(
                    cost_basis=position.cost_basis,
                    quantity=position.quantity,
                    mark_price=price,
                    entered_at=loop.entered_at.get(pair, now),
                    now=now,
                )
            proposals[pair] = await self.proposer.propose(
                strategy_id=strategy.strategy_id,
                pair=pair,
                market=market,
                candles=list(candles.get(pair, ())),
                held=held,
                now=now,
                slot=slot,
                live=self.config,
            )
            # Recorded at once: a crash after a paid call must not pay for the same review again.
            loop = loop.model_copy(update={"last_review_at": {**loop.last_review_at, pair: now}})
            await self.store.save_state(strategy.strategy_id, loop, now)
        return proposals, loop

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

    async def _stamp(self, strategy: StrategyRow, at: datetime) -> datetime:
        """No command may be stamped earlier than the ledger's last activity (a restart in the
        same minute, or a recovery order followed by a new one)."""
        floor = await self.paper.last_activity(strategy.portfolio_id)
        return max(at, floor) if floor is not None else at

    async def _execute(
        self,
        strategy: StrategyRow,
        decision: Decision,
        market: Market,
        at: datetime,
        loop: LoopState,
        policy: Any,
    ) -> tuple[LoopState, str]:
        order = decision.order
        assert order is not None
        slot = int(at.timestamp() // self.config.cycle_seconds)
        key = f"{strategy.strategy_id}:{decision.pair}:{decision.action}:{slot}"
        at = await self._stamp(strategy, at)
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
        # Pre-flight: an order the broker would certainly refuse is logged as a hold and never
        # submitted, so a recurring refusal cannot use up the ledger's capped command history.
        state = await self.paper.get(strategy.portfolio_id)
        refusal = admit(state, intent, policy)
        if refusal is None:
            outcome = simulate(intent, market.quote, policy, at, ())
            if outcome.status not in ("FILLED", "PARTIALLY_FILLED"):
                refusal = outcome.reason
        if refusal is not None:
            await self.store.log_decision(
                strategy.strategy_id,
                decision.pair,
                at,
                decision.action,
                f"preflight_{refusal}",
                decision.detail,
            )
            return loop, "skipped"
        planned = await self.store.plan_decision(
            strategy.strategy_id,
            decision.pair,
            at,
            decision.action,
            decision.reason,
            key,
            intent.model_dump(mode="json"),
            market.entry.model_dump(mode="json"),
            decision.detail,
        )
        if not planned.created:
            # Handled before a restart, or planned by another writer: never submit it again here.
            return loop, ("contended" if planned.status == "planned" else planned.status)
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
            if any(marker in str(exc) for marker in INTEGRITY_MARKERS):
                raise LoopIntegrityError(str(exc)[:200]) from exc
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
