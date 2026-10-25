"""Only one live loop may write at a time: a second is refused, and the lock is released."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.live.errors import LoopIntegrityError
from ai_trading_lab.live.worker import AlreadyRunning, single_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


async def test_a_second_instance_is_refused_until_the_first_exits(engine: AsyncEngine) -> None:
    async with single_instance(engine):
        with pytest.raises(AlreadyRunning):
            async with single_instance(engine):
                raise AssertionError("must not start")
    async with single_instance(engine):  # released on exit, including after an error
        pass
    with pytest.raises(RuntimeError, match="boom"):
        async with single_instance(engine):
            raise RuntimeError("boom")
    async with single_instance(engine):
        pass


async def test_the_lease_notices_when_the_lock_connection_is_lost(engine: AsyncEngine) -> None:
    """If the session holding the lock dies, a second loop may start; the first must stop."""
    async with single_instance(engine) as lease:
        await lease.verify()  # healthy
        async with engine.connect() as other:
            await other.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": lease.backend_pid}
            )
        with pytest.raises(LoopIntegrityError):
            await lease.verify()
    async with single_instance(engine) as lease:  # a new loop can now take the lock
        await lease.verify()
