from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine, database_ready
from ai_trading_lab.reporting.api import router
from ai_trading_lab.reporting.service import ReportingService, Reports

ReadinessCheck = Callable[[], Awaitable[bool]]


class Liveness(BaseModel):
    status: Literal["alive"] = "alive"
    execution_mode: Literal["PAPER_ONLY"] = "PAPER_ONLY"


def create_app(
    settings: Settings | None = None,
    *,
    readiness_check: ReadinessCheck | None = None,
    reporting_service: Reports | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    check = readiness_check

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal check
        app.state.reporting_service = reporting_service
        if readiness_check is not None:
            yield
            return
        engine = create_database_engine(config)
        app.state.reporting_service = reporting_service or ReportingService(engine)

        async def probe() -> bool:
            return await database_ready(engine, config.database_timeout_seconds)

        check = probe
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AI Trading Lab", version="0.1.0", lifespan=lifespan)

    app.include_router(router(config))

    @app.get("/health/live")
    async def live() -> Liveness:
        return Liveness()

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        available = await check() if check is not None else False
        return JSONResponse(
            status_code=200 if available else 503,
            content={
                "status": "ready" if available else "unavailable",
                "database": "available" if available else "unavailable",
                "execution_mode": config.execution_mode,
                "trading_ready": False,
            },
        )

    return app
