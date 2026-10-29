"""Read models for the page: everything is derived from the tables the loop already writes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.ai.budget import BudgetStore
from ai_trading_lab.config import Settings
from ai_trading_lab.dashboard import alerts
from ai_trading_lab.live.ai_strategy import AIConfig
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.journal import ErrorJournal
from ai_trading_lab.live.store import DecisionRow, LiveStore
from ai_trading_lab.paper_execution.store import PaperStore

LABELS = {"benchmark": "Buy and hold", "rules": "Rules breakout", "ai": "AI"}
ORDER = {"rules": 0, "ai": 1, "benchmark": 2}
PAIRS = ("XBTEUR", "ETHEUR", "SOLEUR")
CANDLE_MINUTES = 15
CURVE_POINTS = 400
BREAKER_LIMIT = AIConfig().max_unknown_per_day  # unknown outcomes in 24 h that stop the AI
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _plain(value: Decimal) -> str:
    """No exponent and no trailing zeros: 50050, 0.0004."""
    return format(value.normalize(), "f")


def _decision(row: DecisionRow) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "decision_id": str(row.decision_id),
        "strategy_id": row.strategy_id,
        "at": row.cycle_at_utc.isoformat(),
        "pair": row.pair,
        "action": row.action,
        "reason": row.reason,
        "status": row.status,
        "result": row.result,
        "ai": row.detail,
    }


def _downsample[T](values: list[T], limit: int) -> list[T]:
    if len(values) <= limit:
        return values
    step = (len(values) - 1) / (limit - 1)
    picked = [values[round(i * step)] for i in range(limit)]
    return picked


class DashboardData:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        deployed_commit: str | None = None,
    ) -> None:
        self.engine, self.settings, self.clock = engine, settings, clock
        self.deployed_commit = deployed_commit
        self.store = LiveStore(engine)
        self.paper = PaperStore(engine)
        self.journal = ErrorJournal(engine, clock=clock)
        self.budget = BudgetStore(engine, settings)

    # ---- AI spending and the breaker -------------------------------------------------------

    async def ai(self, now: datetime) -> dict[str, Any]:
        limit = self.settings.ai_total_limit_usd
        active = self.settings.ai_enabled and limit > 0
        spent = held = Decimal(0)
        calls = 0
        times: list[datetime] = []
        for budget_id in await self.budget.budget_ids():
            # A plain connection, not the default locking read: the page must never block the loop.
            async with self.engine.connect() as connection:
                facts = await self.budget.summary(budget_id, snapshot=connection)
            spent += Decimal(str(facts["spent_estimated"]))
            held += Decimal(str(facts["reserved"]))
            calls += int(str(facts["call_count"]))
            times += await self.budget.settled_times(budget_id, now - DAY)
        times.sort()
        used = (spent + held) / limit if limit > 0 else Decimal(0)
        breaker_open = len(times) >= BREAKER_LIMIT
        resumes = (
            times[len(times) - BREAKER_LIMIT] + DAY if breaker_open else None
        )  # when only four remain inside the 24 h window
        return {
            "active": active,
            "currency": "USD",
            "total_limit": str(limit),
            "spent_estimated": _money(spent),
            "held_unresolved": _money(held),
            "used_fraction": str(used.quantize(Decimal("0.0001"))),
            "warn_at": str(alerts.AI_BUDGET_WARN),
            "state": (
                "off"
                if not active
                else "budget_exhausted"
                if used >= alerts.AI_BUDGET_STOP
                else "breaker_open"
                if breaker_open
                else "budget_warning"
                if used >= alerts.AI_BUDGET_WARN
                else "ok"
            ),
            "call_count": calls,
            "unknown_outcomes_24h": len(times),
            "unknown_outcome_times": [t.isoformat() for t in times],
            "breaker_limit": BREAKER_LIMIT,
            "breaker_open": breaker_open,
            "breaker_resumes_at": resumes.isoformat() if resumes else None,
            "cost_basis": "usage estimate, not an invoice",
        }

    # ---- the overview ----------------------------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        now = self.clock()
        initial = LiveConfig().initial_cash
        prices: dict[str, Decimal] = {}
        price_age: dict[str, timedelta | None] = {}
        for pair in PAIRS:
            candles = await self.store.candles(pair, CANDLE_MINUTES, 1)
            if candles:
                prices[pair] = candles[-1].close
                price_age[pair] = now - candles[-1].close_time_utc
            else:
                price_age[pair] = None

        rows = sorted(
            await self.store.strategies(enabled_only=False),
            key=lambda r: (ORDER.get(r.kind, 9), r.strategy_id),
        )
        strategies: list[dict[str, Any]] = []
        curves: dict[str, list[list[str]]] = {}
        newest: datetime | None = None
        first: datetime | None = None
        for row in rows:
            state = await self.paper.get(row.portfolio_id)
            curve = await self.store.equity_curve(row.strategy_id)
            latest = curve[-1] if curve else None
            if curve:
                first = curve[0].at_utc if first is None else min(first, curve[0].at_utc)
                newest = latest.at_utc if newest is None or latest.at_utc > newest else newest  # type: ignore[union-attr]
            curves[row.strategy_id] = [
                [p.at_utc.isoformat(), _money(p.equity)] for p in _downsample(curve, CURVE_POINTS)
            ]
            positions = []
            for p in state.positions:
                pair = p.instrument.native_id
                price = prices.get(pair)
                value = price * p.quantity if price is not None else None
                positions.append(
                    {
                        "pair": pair,
                        "quantity": _plain(p.quantity),
                        "cost_basis": _money(p.cost_basis),
                        "price": _plain(price) if price is not None else None,
                        "value": _money(value) if value is not None else None,
                        "pnl_eur": _money(value - p.cost_basis) if value is not None else None,
                        "pnl_pct": (
                            str(((value / p.cost_basis - 1) * 100).quantize(Decimal("0.01")))
                            if value is not None and p.cost_basis > 0
                            else None
                        ),
                    }
                )
            equity = latest.equity if latest else None
            recent = await self.store.recent_decisions(row.strategy_id, 1)
            last_change = await self.store.audit(row.strategy_id, 1)
            loop_state = await self.store.load_state(row.strategy_id)
            strategies.append(
                {
                    "strategy_id": row.strategy_id,
                    "kind": row.kind,
                    "label": LABELS.get(row.kind, row.kind),
                    "enabled": row.enabled,
                    "start_eur": str(initial),
                    "equity": _money(equity) if equity is not None else None,
                    "equity_at": latest.at_utc.isoformat() if latest else None,
                    "pnl_eur": _money(equity - initial) if equity is not None else None,
                    "pnl_pct": (
                        str(((equity / initial - 1) * 100).quantize(Decimal("0.01")))
                        if equity is not None
                        else None
                    ),
                    "cash": _money(state.cash),
                    "fees_paid": _money(state.fees),
                    "realized_gross": _money(state.realized_gross),
                    "positions": positions,
                    "last_decision": _decision(recent[-1]) if recent else None,
                    "last_change": last_change[0].model_dump(mode="json") if last_change else None,
                    "risk": (
                        {
                            "drawdown_latched": loop_state.risk.drawdown_latched,
                            "daily_suspended": loop_state.risk.daily_suspended,
                            "peak_equity": _money(loop_state.risk.peak_equity),
                        }
                        if loop_state
                        else None
                    ),
                }
            )

        loop_age = None if newest is None else now - newest
        ai = await self.ai(now)
        errors = await self.journal.summary(now)
        failures = await self.journal.recent_error_counts(now - alerts.STRATEGY_FAILURE_WINDOW)
        findings = alerts.evaluate(
            alerts.Facts(
                loop_age=loop_age,
                strategy_failures=failures,
                price_age=price_age,
                ai_active=bool(ai["active"]),
                ai_unknown_24h=int(ai["unknown_outcomes_24h"]),
                ai_breaker_open=bool(ai["breaker_open"]),
                ai_budget_used=Decimal(str(ai["used_fraction"])),
                unacknowledged_errors=errors.unacknowledged_errors,
            )
        )
        run_age = None if first is None else now - first
        return {
            "generated_at": now.isoformat(),
            "paper_only": True,
            "deployed_commit": self.deployed_commit,
            "run": {
                "started_at": first.isoformat() if first else None,
                "age_hours": round(run_age / HOUR, 1) if run_age is not None else None,
            },
            "loop": {
                "healthy": loop_age is not None and loop_age <= alerts.LOOP_STALLED_AFTER,
                "last_cycle_at": newest.isoformat() if newest else None,
                "age_seconds": int(loop_age.total_seconds()) if loop_age is not None else None,
            },
            "prices": {
                pair: {
                    "price": _plain(prices[pair]) if pair in prices else None,
                    "age_seconds": (
                        int(price_age[pair].total_seconds())  # type: ignore[union-attr]
                        if price_age[pair] is not None
                        else None
                    ),
                }
                for pair in PAIRS
            },
            "strategies": strategies,
            "curves": curves,
            "verdict": self._verdict(strategies, run_age),
            "ai": ai,
            "errors": errors.model_dump(mode="json"),
            "alerts": {
                "rules": list(alerts.RULES),
                "firing": [
                    {
                        "rule": f.rule,
                        "severity": f.severity,
                        "title": f.title,
                        "detail": f.detail,
                        "key": f.key,
                        "strategy_id": f.strategy_id,
                    }
                    for f in findings
                ],
                "repeat_after_hours": int(alerts.REPEAT_AFTER / HOUR),
            },
        }

    @staticmethod
    def _verdict(strategies: list[dict[str, Any]], run_age: timedelta | None) -> dict[str, Any]:
        ranked = sorted(
            (s for s in strategies if s["equity"] is not None),
            key=lambda s: Decimal(s["equity"]),
            reverse=True,
        )
        standings = [
            {"strategy_id": s["strategy_id"], "label": s["label"], "equity": s["equity"]}
            for s in ranked
        ]
        if len(ranked) < 2 or run_age is None:
            return {"state": "too_early", "why": "no_data", "standings": standings, "gap_eur": None}
        gap = Decimal(ranked[0]["equity"]) - Decimal(ranked[1]["equity"])
        if run_age < alerts.YOUNG_RUN:
            why = "run_under_72h"
        elif gap < alerts.CLOSE_RACE_EUR:
            why = "gap_under_1_eur"
        else:
            return {
                "state": "leader",
                "leader": ranked[0]["strategy_id"],
                "label": ranked[0]["label"],
                "gap_eur": _money(gap),
                "standings": standings,
            }
        return {"state": "too_early", "why": why, "gap_eur": _money(gap), "standings": standings}

    # ---- lists -----------------------------------------------------------------------------

    async def decisions(
        self,
        *,
        strategy_id: str | None,
        pair: str | None,
        action: str | None,
        before_seq: int | None,
        limit: int,
    ) -> dict[str, Any]:
        rows = await self.store.decisions_page(
            strategy_id=strategy_id,
            pair=pair,
            action=action,
            before_seq=before_seq,
            limit=limit,
        )
        return {
            "decisions": [_decision(r) for r in rows],
            "next_before_seq": rows[-1].seq if len(rows) == limit else None,
        }

    async def audit(self, limit: int) -> dict[str, Any]:
        rows = await self.store.audit(limit=limit)
        return {"audit": [r.model_dump(mode="json") for r in rows]}

    async def error_list(
        self, *, status: str | None, level: str | None, limit: int
    ) -> dict[str, Any]:
        groups = await self.journal.groups(
            status=status,  # type: ignore[arg-type]
            level=level,  # type: ignore[arg-type]
            limit=limit,
        )
        return {
            "summary": (await self.journal.summary()).model_dump(mode="json"),
            "problems": [
                g.model_dump(mode="json", exclude={"last_traceback", "last_context"})
                for g in groups
            ],
        }

    async def error_detail(self, fingerprint: str) -> dict[str, Any] | None:
        group = await self.journal.group(fingerprint)
        if group is None:
            return None
        seen = await self.journal.occurrences(group.fingerprint)
        return {
            "problem": group.model_dump(mode="json"),
            "recent_occurrences": [o.model_dump(mode="json") for o in seen],
        }
