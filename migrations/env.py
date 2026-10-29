import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from ai_trading_lab.ai.run_store import ai_runs
from ai_trading_lab.alerts.state import alert_state
from ai_trading_lab.backtesting.store import research_runs
from ai_trading_lab.config import Settings
from ai_trading_lab.experiments.store import experiments
from ai_trading_lab.live.journal import error_groups
from ai_trading_lab.live.store import live_strategies
from ai_trading_lab.notifications.store import notifications
from ai_trading_lab.runtime.journal import sessions

metadata = ai_runs.metadata
assert (
    metadata
    is research_runs.metadata
    is experiments.metadata
    is notifications.metadata
    is sessions.metadata
    is live_strategies.metadata
    is error_groups.metadata
    is alert_state.metadata
)


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(
        Settings().database_url.get_secret_value(), poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(), target_metadata=metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
