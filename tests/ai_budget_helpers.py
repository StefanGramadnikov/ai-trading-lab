"""Remove only a test's new budget records after intentional uncertainty/corruption."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.ai.budget import budgets, calls


@asynccontextmanager
async def isolated_budgets(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.connect() as connection:
        before = set((await connection.execute(select(budgets.c.budget_id))).scalars())
    try:
        yield
    finally:
        async with engine.begin() as connection:
            after = set((await connection.execute(select(budgets.c.budget_id))).scalars())
            owned = after - before
            await connection.execute(delete(calls).where(calls.c.budget_id.in_(owned)))
            await connection.execute(delete(budgets).where(budgets.c.budget_id.in_(owned)))
