"""Authenticated read-only routes; credentials never reach browser JavaScript."""

import json
import secrets
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.reporting.service import MAX_EXPORT_BYTES, Reports


def router(settings: Settings) -> APIRouter:
    basic = HTTPBasic(auto_error=False)

    def authenticated(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
        if settings.reporting_username is None or settings.reporting_password is None:
            raise HTTPException(503, "Reporting access is not configured")
        expected = settings.reporting_password.get_secret_value().encode()
        username = credentials.username.encode() if credentials else b""
        password = credentials.password.encode() if credentials else b""
        valid_user = secrets.compare_digest(username, settings.reporting_username.encode())
        valid_password = secrets.compare_digest(password, expected)
        if not (credentials and valid_user and valid_password):
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Basic"}
            )

    result = APIRouter(dependencies=[Depends(authenticated)])
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        ),
        "Referrer-Policy": "no-referrer",
    }
    assets = Path(__file__).parent / "static"

    @result.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(assets / "index.html", headers=headers)

    @result.get("/dashboard/{asset}", include_in_schema=False)
    async def static(asset: str) -> FileResponse:
        if asset not in {"app.js", "style.css"}:
            raise HTTPException(404, "Asset not found")
        return FileResponse(assets / asset, headers=headers)

    def service(request: Request) -> Reports:
        value: Reports | None = getattr(request.app.state, "reporting_service", None)
        if value is None:
            raise HTTPException(503, "Reporting unavailable")
        return value

    def response(value: object) -> Response:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_EXPORT_BYTES:
            raise ValueError("response too large")
        return Response(encoded, media_type="application/json", headers=headers)

    def error(exc: Exception) -> HTTPException:
        if isinstance(exc, LookupError):
            return HTTPException(404, "Experiment not found")
        if isinstance(exc, ValueError):
            return HTTPException(409, "Report integrity or runtime compatibility check failed")
        return HTTPException(503, "Reporting unavailable")

    @result.get("/api/reports/status")
    async def status(request: Request) -> Response:
        try:
            return response(await service(request).status())
        except (SQLAlchemyError, TimeoutError, OSError, ValueError) as exc:
            raise error(exc) from None

    @result.get("/api/reports/experiments")
    async def listing(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> Response:
        try:
            return response(await service(request).list_experiments(limit, offset))
        except (SQLAlchemyError, TimeoutError, OSError, ValueError) as exc:
            raise error(exc) from None

    @result.get("/api/reports/experiments/{experiment_id}")
    async def detail(request: Request, experiment_id: UUID) -> Response:
        try:
            return response(await service(request).detail(experiment_id))
        except (SQLAlchemyError, TimeoutError, OSError, ValueError, LookupError) as exc:
            raise error(exc) from None

    @result.get("/api/reports/experiments/{experiment_id}/export")
    async def export(request: Request, experiment_id: UUID) -> Response:
        try:
            data = await service(request).export(experiment_id)
            return Response(
                data,
                media_type="application/json",
                headers={
                    **headers,
                    "Content-Disposition": (
                        f'attachment; filename="paper-audit-{experiment_id}.json"'
                    ),
                },
            )
        except (SQLAlchemyError, TimeoutError, OSError, ValueError, LookupError) as exc:
            raise error(exc) from None

    @result.get("/api/reports/runtime")
    async def runtimes(
        request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> Response:
        try:
            return response(await service(request).runtime_sessions(limit))
        except (SQLAlchemyError, TimeoutError, OSError, ValueError) as exc:
            raise error(exc) from None

    @result.get("/api/reports/runtime/{session_id}")
    async def runtime_detail(request: Request, session_id: UUID) -> Response:
        try:
            return response(await service(request).runtime_detail(session_id))
        except (SQLAlchemyError, TimeoutError, OSError, ValueError, LookupError) as exc:
            raise error(exc) from None

    return result
