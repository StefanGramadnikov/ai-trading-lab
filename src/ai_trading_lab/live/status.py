"""A plain-data snapshot of every strategy, for the CLI now and the dashboard later."""

from __future__ import annotations

from typing import Any

from ai_trading_lab.live.engine import LiveEngine


async def summary(live: LiveEngine, *, recent: int = 8) -> dict[str, Any]:
    """Strategies, their holdings, latest equity and most recent decisions."""
    out: dict[str, Any] = {"paper_only": True, "strategies": []}
    for row in await live.store.strategies(enabled_only=False):
        state = await live.paper.get(row.portfolio_id)
        latest = await live.store.latest_equity(row.strategy_id)
        decisions = await live.store.decisions(row.strategy_id, limit=100000)
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
                "decisions_logged": len(decisions),
                "recent_decisions": [
                    {
                        "at": d.cycle_at_utc.isoformat(),
                        "pair": d.pair,
                        "action": d.action,
                        "reason": d.reason,
                        "status": d.status,
                    }
                    for d in decisions[-recent:]
                ],
            }
        )
    return out
