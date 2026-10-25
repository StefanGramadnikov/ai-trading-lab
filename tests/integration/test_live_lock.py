"""Only one live loop may write at a time: a second is refused, and the lock is released."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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
