"""Builds the evaluation report from the tables the loop writes.

Reads only: it never writes and never locks anything the trading loop needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.config import Settings
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.live.store import EquityPoint, LiveStore
from ai_trading_lab.report import metrics, verdict
from ai_trading_lab.report.models import (
    AICost,
    EvaluationReport,
    Integrity,
    PairedResult,
    StrategyResult,
)

LABELS = {"benchmark": "Buy and hold", "rules": "Rules breakout", "ai": "AI"}
ORDER = {"rules": 0, "ai": 1, "benchmark": 2}
CYCLE_SECONDS = 900
MAX_POINTS = 5000  # about 52 days of 15-minute equity points per strategy
MAX_DECISIONS = 20000
STALE_AFTER = timedelta(
    minutes=45
)  # three cycles without a new point means the data is not current
THRESHOLDS = {
    "minimum_window_days": str(verdict.MIN_WINDOW_DAYS),
    "minimum_closed_ai_trades": str(verdict.MIN_CLOSED_TRADES),
    "minimum_loop_coverage": str(verdict.MIN_COVERAGE),
    "margin_over_buy_and_hold_percentage_points": str(verdict.MARGIN_POINTS),
    "drawdown_limit": str(verdict.DRAWDOWN_LIMIT),
}


class ReportService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.settings, self.clock = engine, settings, clock
        self.live = LiveStore(engine)
        self.budget = BudgetStore(engine, settings)
        self.journal = ErrorJournal(engine, clock=clock)

    @asynccontextmanager
    async def _snapshot(self) -> AsyncIterator[AsyncConnection]:
        """One repeatable, read-only view: the AI loop may commit between two plain reads."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            yield connection

    async def _ai_cost(self, window: metrics.Window | None, usd_to_eur: Decimal) -> AICost:
        spent = held = Decimal(0)
        calls = 0
        if window is not None:
            async with self._snapshot() as snapshot:
                for budget_id in await self.budget.budget_ids():
                    # (start, end] like the trades: a call is stamped with its cycle's time
                    for fact in await self.budget.calls_between(
                        budget_id,
                        window.start + timedelta(microseconds=1),
                        window.end + timedelta(microseconds=1),
                        snapshot=snapshot,
                    ):
                        calls += 1
                        spent += fact.amount
                        held += fact.held
        return AICost(
            calls=calls,
            spent_usd=spent,
            held_usd=held,
            usd_to_eur=usd_to_eur,
            cost_eur=(spent + held) * usd_to_eur,
        )

    async def build(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        usd_to_eur: Decimal = Decimal(1),
    ) -> EvaluationReport:
        now = self.clock()
        rows = sorted(
            await self.live.strategies(enabled_only=False),
            key=lambda r: (ORDER.get(r.kind, 9), r.strategy_id),
        )
        series: dict[str, list[EquityPoint]] = {
            r.strategy_id: await self.live.equity_curve(r.strategy_id, MAX_POINTS) for r in rows
        }
        series = {sid: pts for sid, pts in series.items() if pts}
        # One strategy per kind: if an old row exists too, the one still recording wins.
        ignored: list[str] = []
        newest: dict[str, str] = {}
        for r in rows:
            if r.strategy_id in series:
                best = newest.get(r.kind)
                if best is None or series[r.strategy_id][-1].at_utc > series[best][-1].at_utc:
                    newest[r.kind] = r.strategy_id
        for r in rows:
            if r.strategy_id in series and newest[r.kind] != r.strategy_id:
                ignored.append(r.strategy_id)
                del series[r.strategy_id]
        truncated = any(len(pts) >= MAX_POINTS for pts in series.values())
        window = metrics.common_window(series, since, until)
        results: dict[str, StrategyResult] = {}
        daily: dict[str, dict[date, Decimal]] = {}
        switches = 0
        for row in rows:
            if window is None or row.strategy_id not in series:
                continue
            points = metrics.clip(series[row.strategy_id], window)
            # (start, end]: equity is recorded after a cycle's fills, so what happened in the very
            # first cycle is already inside the starting equity and must not be counted twice.
            decisions = [
                d
                for d in await self.live.decisions_between(
                    row.strategy_id,
                    window.start,
                    window.end + timedelta(microseconds=1),
                    limit=MAX_DECISIONS,
                )
                if d.cycle_at_utc > window.start
            ]
            truncated = truncated or len(decisions) >= MAX_DECISIONS
            filled = [
                d for d in decisions if d.status in ("filled", "partial") and d.action != "hold"
            ]
            state = await self.live.load_state(row.strategy_id)
            change = metrics.pnl(points)
            start = points[0].equity if points else None
            results[row.kind] = StrategyResult(
                strategy_id=row.strategy_id,
                kind=row.kind,
                label=LABELS.get(row.kind, row.kind),
                start_equity=start,
                end_equity=points[-1].equity if points else None,
                pnl=change,
                pnl_pct=(change / start * 100) if change is not None and start else None,
                max_drawdown=metrics.max_drawdown(points),
                entries=sum(1 for d in filled if d.action == "buy"),
                closed_trades=sum(1 for d in filled if d.action == "sell"),
                fees=sum(
                    (Decimal(str((d.result or {}).get("fee", 0))) for d in filled), Decimal(0)
                ),
                turnover=sum(
                    (Decimal(str((d.result or {}).get("notional", 0))) for d in filled), Decimal(0)
                ),
                drawdown_latched=bool(state and state.risk.drawdown_latched),
            )
            daily[row.kind] = metrics.daily_changes(points)
            switches += sum(
                1
                for a in await self.live.audit(row.strategy_id, 200)
                if window.start <= a.at_utc <= window.end
            )
        paired = []
        for a, b in (("ai", "benchmark"), ("ai", "rules"), ("rules", "benchmark")):
            if a in daily and b in daily:
                p = metrics.paired(daily[a], daily[b])
                paired.append(
                    PairedResult(
                        label=LABELS[a],
                        versus=LABELS[b],
                        days=p.days,
                        better_days=p.better_days,
                        mean_diff=p.mean_diff,
                        std_error=p.std_error,
                    )
                )
        ai_cost = await self._ai_cost(window if "ai" in results else None, usd_to_eur)
        ai = results.get("ai")
        coverage = Decimal(0)
        if window is not None and results:
            expected = Decimal(
                int((window.end - window.start).total_seconds()) // CYCLE_SECONDS + 1
            )
            worst = min(len(metrics.clip(series[r.strategy_id], window)) for r in results.values())
            coverage = min(Decimal(1), Decimal(worst) / expected)
        errors = await self.journal.summary(now)
        window_days = window.days if window is not None else Decimal(0)
        vs_bench = next(
            (p for p in paired if p.label == LABELS["ai"] and p.versus == LABELS["benchmark"]),
            None,
        )
        stale = bool(until is None and window is not None and now - window.end > STALE_AFTER)
        decision = verdict.decide(
            ai=ai,
            rules=results.get("rules"),
            benchmark=results.get("benchmark"),
            ai_cost_eur=ai_cost.cost_eur,
            window_days=window_days,
            coverage=coverage,
            unacknowledged_errors=errors.unacknowledged_errors,
            switches_in_window=switches,
            paired_vs_benchmark=vs_bench,
            stale=stale,
            truncated=truncated,
        )
        return EvaluationReport(
            generated_at=now,
            window_start=window.start if window else None,
            window_end=window.end if window else None,
            window_days=window_days,
            strategies=sorted(results.values(), key=lambda s: ORDER.get(s.kind, 9)),
            paired=paired,
            ai_cost=ai_cost,
            ai_net_pnl=(ai.pnl - ai_cost.cost_eur)
            if ai is not None and ai.pnl is not None
            else None,
            integrity=Integrity(
                coverage=coverage,
                unacknowledged_errors=errors.unacknowledged_errors,
                errors_24h=errors.errors_24h,
                switches_in_window=switches,
                stale=stale,
                truncated=truncated,
                ignored_strategies=ignored,
            ),
            verdict=decision,
            thresholds=THRESHOLDS,
            assumptions=assumptions(usd_to_eur),
        )


def assumptions(usd_to_eur: Decimal) -> list[str]:
    return [
        "Every strategy started with EUR 100 of paper money. The window starts when the youngest "
        "strategy started, so all three are compared over the same time.",
        "Equity already includes trading fees and the simulated spread. The AI provider's bill is "
        f"not in it: it is netted at 1 USD = {usd_to_eur} EUR (1 is a conservative assumption).",
        "Fills are simulated from the public order book; real fills could be worse. Nothing here "
        "is a real trade.",
        "Daily comparisons use UTC days; the first and last day can be partial. Returns are "
        "compared in percent of each strategy's own starting equity inside the window.",
        "GO does not adjust for risk: sitting in cash through a falling week can look like "
        "out-performance. 'Closed trades' counts filled sells (partial fills and stop exits "
        "included), not clean round trips. The AI bill sums every budget envelope in the database.",
        "A few days of data is a small sample. This report does not prove skill; GO means "
        "'worth proposing a small real-money pilot for a separate decision', never approval.",
        "The GO / EXTEND / STOP thresholds are the assistant's proposal (DEC-049) and need the "
        "owner's approval.",
    ]
