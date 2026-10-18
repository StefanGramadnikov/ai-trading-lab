import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_timeout=settings.database_timeout_seconds,
        connect_args={"connect_timeout": max(1, int(settings.database_timeout_seconds))},
    )


async def database_ready(engine: AsyncEngine, deadline_seconds: float) -> bool:
    try:
        async with asyncio.timeout(deadline_seconds), engine.connect() as connection:
            result = await connection.scalar(text("SELECT 1"))
            return bool(result == 1)
    except (SQLAlchemyError, TimeoutError, OSError):
        return False
