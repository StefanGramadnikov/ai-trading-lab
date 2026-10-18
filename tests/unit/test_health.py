import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.app import create_app
from ai_trading_lab.config import Settings
from ai_trading_lab.database import database_ready


def test_liveness_never_queries_database() -> None:
    check = AsyncMock(return_value=False)
    with TestClient(create_app(Settings(_env_file=None), readiness_check=check)) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive", "execution_mode": "PAPER_ONLY"}
    check.assert_not_awaited()


@pytest.mark.parametrize("ready, code", [(True, 200), (False, 503)])
def test_readiness(ready: bool, code: int) -> None:
    check = AsyncMock(return_value=ready)
    with TestClient(create_app(Settings(_env_file=None), readiness_check=check)) as client:
        response = client.get("/health/ready")
    assert response.status_code == code
    assert response.json() == {
        "status": "ready" if ready else "unavailable",
        "database": "available" if ready else "unavailable",
        "execution_mode": "PAPER_ONLY",
        "trading_ready": False,
    }
    check.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [SQLAlchemyError("sensitive connection info"), TimeoutError()])
async def test_database_probe_handles_failure(failure: Exception) -> None:
    engine = AsyncMock(spec=AsyncEngine)
    engine.connect.side_effect = failure
    assert await database_ready(engine, 0.05) is False


@pytest.mark.asyncio
async def test_database_probe_has_deadline() -> None:
    engine = AsyncMock(spec=AsyncEngine)

    @asynccontextmanager
    async def slow_connect() -> AsyncIterator[Any]:
        await asyncio.sleep(1)
        yield AsyncMock()

    engine.connect.side_effect = slow_connect
    assert await database_ready(engine, 0.01) is False


def test_app_disposes_owned_engine() -> None:
    from unittest.mock import patch

    engine = AsyncMock(spec=AsyncEngine)
    with patch("ai_trading_lab.app.create_database_engine", return_value=engine):
        with TestClient(create_app(Settings(_env_file=None))) as client:
            assert client.get("/health/live").status_code == 200
    engine.dispose.assert_awaited_once()
