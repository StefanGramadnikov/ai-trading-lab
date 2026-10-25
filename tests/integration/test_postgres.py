import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ai_trading_lab.app import create_app
from ai_trading_lab.config import Settings
from ai_trading_lab.database import database_ready

pytestmark = pytest.mark.integration


def test_readiness(database_url: str) -> None:
    settings = Settings(database_url=database_url, _env_file=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200


@pytest.mark.asyncio
async def test_connectivity_and_rollback(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        assert await database_ready(engine, 2) is True
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0013_live_decision_detail"
            )
            await connection.execute(text("CREATE TEMP TABLE m1_probe (value INTEGER)"))
            await connection.commit()
            await connection.execute(text("INSERT INTO m1_probe VALUES (1)"))
            await connection.rollback()
            assert await connection.scalar(text("SELECT count(*) FROM m1_probe")) == 0
    finally:
        await engine.dispose()


def test_unreachable_database_does_not_break_liveness() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/ai_trading_lab_test",
        database_timeout_seconds=0.1,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert "unused" not in response.text
