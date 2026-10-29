"""The dashboard web app (paper only). Reached through an SSH tunnel; one signed-in owner.

Every `/api` route needs the session cookie; every write also needs the CSRF header and a same-site
Origin. Writes call the same store functions as `atl-live enable|disable|ack`, so the audit trail is
one table whichever way a change was made.
"""

import argparse
import asyncio
import getpass
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_trading_lab.config import Settings
from ai_trading_lab.dashboard.auth import (
    COOKIE_NAME,
    MAX_PASSWORD_LENGTH,
    LoginThrottle,
    Session,
    SessionSigner,
    hash_password,
    verify_password,
)
from ai_trading_lab.dashboard.queries import DashboardData
from ai_trading_lab.database import create_database_engine

STATIC = Path(__file__).parent / "static"
ACTOR = "dashboard:owner"
FINGERPRINT = re.compile(r"[0-9a-f]{4,64}")
STRATEGY_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class Toggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    reason: str = Field(min_length=1, max_length=200)


class Acknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=200)


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "message": message, **extra}, headers=HEADERS
    )


def create_dashboard_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    configured = (
        config.dashboard_password_hash is not None and config.dashboard_session_secret is not None
    )
    signer = (
        SessionSigner(
            config.dashboard_session_secret.get_secret_value().encode(),
            idle_seconds=config.dashboard_idle_minutes * 60,
        )
        if config.dashboard_session_secret is not None
        else None
    )
    throttle = LoginThrottle()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine if engine is not None else create_database_engine(config)
        app.state.data = DashboardData(
            owned, config, clock=clock, deployed_commit=config.deployed_commit
        )
        try:
            yield
        finally:
            if engine is None:
                await owned.dispose()

    app = FastAPI(
        title="AI Trading Lab dashboard",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def data_of(request: Request) -> DashboardData:
        data: DashboardData = request.app.state.data
        return data

    def session_of(request: Request, response: Response) -> Session:
        """The signed-in session, renewed on every call so the idle timeout slides."""
        if signer is None:
            raise HTTPException(503, "dashboard_not_configured")
        session = signer.read(request.cookies.get(COOKIE_NAME), now=wall())
        if session is None:
            raise HTTPException(401, "not_signed_in")
        _set_cookie(response, signer.issue(now=wall(), previous=session))
        for name, value in HEADERS.items():
            response.headers[name] = value
        return session

    def writer_of(request: Request, session: Annotated[Session, Depends(session_of)]) -> Session:
        """A write also needs the CSRF header and, if the browser sent one, a same-host Origin."""
        token = request.headers.get("x-csrf-token", "")
        if not token or token != session.csrf:
            raise HTTPException(403, "csrf")
        origin = request.headers.get("origin")
        if origin is not None and origin.split("://", 1)[-1] != request.headers.get("host"):
            raise HTTPException(403, "cross_origin")
        return session

    def renewed(out: Response, session: Session) -> Response:
        """A response built by hand does not carry the dependency's cookie, so renew it here."""
        assert signer is not None
        _set_cookie(out, signer.issue(now=wall(), previous=session))
        return out

    def _set_cookie(response: Response, value: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            value,
            max_age=12 * 3600,
            httponly=True,
            samesite="strict",
            path="/",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {
            "not_signed_in": "Sign in to continue.",
            "csrf": "The page is out of date. Reload it and try again.",
            "cross_origin": "Request refused.",
            "dashboard_not_configured": "No dashboard password is configured on the server.",
        }
        code = str(exc.detail)
        return _error(exc.status_code, code, messages.get(code, code))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return _error(
            503,
            "database_unavailable",
            "The database did not answer. Nothing was changed.",
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        try:
            await asyncio.wait_for(
                data_of(request).journal.record_exception("dashboard", exc, at=clock()), 5
            )
        except Exception:  # noqa: BLE001 - recording must never turn one failure into two
            print("dashboard: could not record an error", file=sys.stderr, flush=True)
        return _error(500, "internal", "Something went wrong. It was recorded on the Errors page.")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive", "execution_mode": "PAPER_ONLY"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers=HEADERS)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.post("/api/login")
    async def login(body: Login, request: Request, response: Response) -> Response:
        if not configured or signer is None or config.dashboard_password_hash is None:
            raise HTTPException(503, "dashboard_not_configured")
        wait = throttle.retry_after(monotonic())
        if wait:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "too_many_attempts",
                    "message": f"Too many wrong passwords. Try again in {wait} seconds.",
                    "retry_after": wait,
                },
                headers={**HEADERS, "Retry-After": str(wait)},
            )
        # Count the attempt before the slow check, so parallel guesses cannot slip past the limit.
        throttle.failed(monotonic())
        # scrypt takes a moment and blocks: keep it off the event loop.
        ok = await asyncio.to_thread(
            verify_password, body.password, config.dashboard_password_hash.get_secret_value()
        )
        if not ok:
            return _error(401, "wrong_password", "That password is not right.")
        throttle.succeeded()
        cookie = signer.issue(now=wall())
        session = signer.read(cookie, now=wall())
        assert session is not None
        out = JSONResponse({"ok": True, "csrf": session.csrf, "user": "owner"}, headers=HEADERS)
        _set_cookie(out, cookie)
        return out

    @app.post("/api/logout")
    async def logout() -> Response:
        out = JSONResponse({"ok": True}, headers=HEADERS)
        out.delete_cookie(COOKIE_NAME, path="/")
        return out

    @app.get("/api/me")
    async def me(session: Annotated[Session, Depends(session_of)]) -> dict[str, Any]:
        return {
            "user": "owner",
            "csrf": session.csrf,
            "idle_minutes": config.dashboard_idle_minutes,
        }

    @app.get("/api/overview")
    async def overview(
        request: Request, _: Annotated[Session, Depends(session_of)]
    ) -> dict[str, Any]:
        return await data_of(request).overview()

    @app.get("/api/decisions")
    async def decisions(
        request: Request,
        _: Annotated[Session, Depends(session_of)],
        strategy_id: Annotated[str | None, Query(pattern=f"^{STRATEGY_ID.pattern}$")] = None,
        pair: Annotated[str | None, Query(pattern=r"^[A-Z]{3,10}$")] = None,
        action: Annotated[str | None, Query(pattern=r"^[a-z_]{1,20}$")] = None,
        before_seq: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return await data_of(request).decisions(
            strategy_id=strategy_id,
            pair=pair,
            action=action,
            before_seq=before_seq,
            limit=limit,
        )

    @app.get("/api/audit")
    async def audit(
        request: Request,
        _: Annotated[Session, Depends(session_of)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return await data_of(request).audit(limit)

    @app.get("/api/errors")
    async def errors(
        request: Request,
        _: Annotated[Session, Depends(session_of)],
        status: Annotated[str | None, Query(pattern=r"^(new|seen|acknowledged)$")] = None,
        level: Annotated[str | None, Query(pattern=r"^(error|warning)$")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return await data_of(request).error_list(status=status, level=level, limit=limit)

    @app.get("/api/errors/{fingerprint}")
    async def error_detail(
        fingerprint: str, request: Request, _: Annotated[Session, Depends(session_of)]
    ) -> dict[str, Any]:
        if not FINGERPRINT.fullmatch(fingerprint):
            raise HTTPException(404, "not_found")
        found = await data_of(request).error_detail(fingerprint)
        if found is None:
            raise HTTPException(404, "not_found")
        return found

    @app.post("/api/strategies/{strategy_id}/enabled")
    async def toggle(
        strategy_id: str,
        body: Toggle,
        request: Request,
        session: Annotated[Session, Depends(writer_of)],
    ) -> Response:
        if not STRATEGY_ID.fullmatch(strategy_id):
            raise HTTPException(404, "not_found")
        reason = body.reason.strip()
        if not reason:
            return _error(422, "reason_required", "Say why: it is recorded in the audit trail.")
        data = data_of(request)
        try:
            changed = await data.store.set_enabled(
                strategy_id, body.enabled, actor=ACTOR, reason=reason, at=clock()
            )
        except ValueError:
            raise HTTPException(404, "not_found") from None
        audit_rows = await data.store.audit(strategy_id, 1)
        return renewed(
            JSONResponse(
                {
                    "strategy_id": strategy_id,
                    "enabled": body.enabled,
                    "changed": changed,
                    "audit": audit_rows[0].model_dump(mode="json") if audit_rows else None,
                },
                headers=HEADERS,
            ),
            session,
        )

    @app.post("/api/errors/{fingerprint}/ack")
    async def acknowledge(
        fingerprint: str,
        body: Acknowledge,
        request: Request,
        session: Annotated[Session, Depends(writer_of)],
    ) -> Response:
        if not FINGERPRINT.fullmatch(fingerprint):
            raise HTTPException(404, "not_found")
        reason = body.reason.strip()
        if not reason:
            return _error(422, "reason_required", "Say why: it is recorded.")
        try:
            count = await data_of(request).journal.acknowledge(
                fingerprint, actor=ACTOR, reason=reason, at=clock()
            )
        except ValueError:
            return _error(409, "ambiguous", "That prefix matches more than one problem.")
        return renewed(JSONResponse({"acknowledged": count}, headers=HEADERS), session)

    return app


def factory() -> FastAPI:
    return create_dashboard_app()


def main() -> None:
    """`hash-password` prints the value for ATL_DASHBOARD_PASSWORD_HASH and `serve` runs the page.

    The password is typed at a hidden prompt, never given as an argument.
    """
    parser = argparse.ArgumentParser(description="V0 dashboard (PAPER_ONLY)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hash-password", help="prompt for a password and print its hash")
    commands.add_parser("make-secret", help="print a random session secret")
    serve = commands.add_parser("serve", help="run the dashboard")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - published to 127.0.0.1 only
    serve.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    if arguments.command == "hash-password":
        first = getpass.getpass("New dashboard password (12+ characters): ")
        if first != getpass.getpass("Repeat it: "):
            print("The two passwords differ.", file=sys.stderr)
            sys.exit(2)
        try:
            print(hash_password(first))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
    elif arguments.command == "make-secret":
        import secrets

        print(secrets.token_hex(32))
    else:
        import uvicorn

        uvicorn.run(
            "ai_trading_lab.dashboard.app:factory",
            factory=True,
            host=arguments.host,
            port=arguments.port,
            log_level="info",
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
