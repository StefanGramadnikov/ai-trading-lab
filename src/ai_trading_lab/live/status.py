"""A plain-data snapshot of every strategy, for the CLI now and the dashboard later."""

from __future__ import annotations

from typing import Any

from ai_trading_lab.live.engine import LiveEngine


async def summary(live: LiveEngine, *, recent: int = 8) -> dict[str, Any]:
    """Strategies, their holdings, latest equity and most recent decisions."""
    out: dict[str, Any] = {"paper_only": True, "strategies": []}
    own = live.strategy_ids
    for row in await live.store.strategies(enabled_only=False):
        if row.strategy_id not in own:
            continue
        state = await live.paper.get(row.portfolio_id)
        latest = await live.store.latest_equity(row.strategy_id)
        logged = await live.store.decision_count(row.strategy_id)
        recent_rows = await live.store.recent_decisions(row.strategy_id, recent)
        out["strategies"].append(
            {
                "strategy_id": row.strategy_id,
                "kind": row.kind,
                "enabled": row.enabled,
                "cash": str(state.cash),
                "fees_paid": str(state.fees),
                "realized_gross": str(state.realized_gross),
                "positions": [
                    {
                        "instrument": p.instrument.native_id,
                        "quantity": str(p.quantity),
                        "cost_basis": str(p.cost_basis),
                    }
                    for p in state.positions
                ],
                "equity": str(latest.equity) if latest else None,
                "equity_at": latest.at_utc.isoformat() if latest else None,
                "decisions_logged": logged,
                "recent_decisions": [
                    {
                        "at": d.cycle_at_utc.isoformat(),
                        "pair": d.pair,
                        "action": d.action,
                        "reason": d.reason,
                        "status": d.status,
                        "ai": d.detail,
                    }
                    for d in recent_rows
                ],
            }
        )
    return out
