from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.app import create_app
from ai_trading_lab.config import Settings


def client(service: Any, *, configured: bool = True) -> TestClient:
    settings = Settings(
        _env_file=None,
        reporting_username="reader" if configured else None,
        reporting_password=SecretStr("local-test-password") if configured else None,
    )
    return TestClient(
        create_app(
            settings, readiness_check=AsyncMock(return_value=True), reporting_service=service
        )
    )


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/app.js",
        "/dashboard/style.css",
        "/api/reports/status",
        "/api/reports/runtime",
        f"/api/reports/runtime/{uuid4()}",
        "/api/reports/experiments",
        f"/api/reports/experiments/{uuid4()}",
        f"/api/reports/experiments/{uuid4()}/export",
    ],
)
def test_auth_precedes_data_access(path: str) -> None:
    service = AsyncMock()
    with client(service) as app:
        assert app.get(path).status_code == 401
        assert app.get(path, auth=("reader", "wrong")).status_code == 401
    assert not service.mock_calls


def test_disabled_and_invalid_credentials_fail_closed() -> None:
    with client(AsyncMock(), configured=False) as app:
        assert app.get("/api/reports/status").status_code == 503
    with pytest.raises(ValueError):
        Settings(_env_file=None, reporting_username="reader")


def test_list_bounds_and_authenticated_detail_export() -> None:
    eid = uuid4()
    service = AsyncMock()
    service.list_experiments.return_value = {"items": [], "limit": 20, "offset": 0}
    service.detail.return_value = {
        "experiment_id": str(eid),
        "economic_net_pnl": "0.10000000000001",
    }
    service.export.return_value = b'{"audit":"bounded"}'
    with client(service) as app:
        app.auth = ("reader", "local-test-password")
        assert app.get("/api/reports/experiments").json()["items"] == []
        assert app.get("/api/reports/experiments?limit=101").status_code == 422
        assert app.get("/api/reports/experiments?offset=-1").status_code == 422
        assert (
            app.get(f"/api/reports/experiments/{eid}").json()["economic_net_pnl"]
            == "0.10000000000001"
        )
        response = app.get(f"/api/reports/experiments/{eid}/export")
        assert response.content == b'{"audit":"bounded"}'
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store"
        assert app.post(f"/api/reports/experiments/{eid}").status_code == 405


@pytest.mark.parametrize(
    "error,status",
    [
        (ValueError("sensitive provenance"), 409),
        (SQLAlchemyError("secret db url"), 503),
        (TimeoutError("secret"), 503),
        (LookupError("missing"), 404),
    ],
)
def test_errors_are_sanitized(error: Exception, status: int) -> None:
    service = AsyncMock()
    service.detail.side_effect = error
    with client(service) as app:
        response = app.get(
            f"/api/reports/experiments/{uuid4()}", auth=("reader", "local-test-password")
        )
        assert response.status_code == status
        assert "secret" not in response.text and "sensitive" not in response.text


def test_password_character_contract_is_enforced_before_startup() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        Settings(
            _env_file=None,
            reporting_username="reader",
            reporting_password=SecretStr("éabcdefghijklm"),
        )
    settings = Settings(
        _env_file=None,
        reporting_username="reader",
        reporting_password=SecretStr("ASCII-punctuation!23"),
    )
    service = AsyncMock()
    service.status.return_value = {"execution_mode": "PAPER_ONLY"}
    with TestClient(
        create_app(
            settings, readiness_check=AsyncMock(return_value=True), reporting_service=service
        )
    ) as app:
        assert (
            app.get("/api/reports/status", auth=("reader", "ASCII-punctuation!23")).status_code
            == 200
        )


def test_authenticated_runtime_endpoints_are_bounded_and_read_only() -> None:
    service = AsyncMock()
    service.runtime_sessions.return_value = {"items": [], "limit": 20}
    service.runtime_detail.return_value = {"state": {"paper_only": True, "ai_enabled": False}}
    with client(service) as app:
        app.auth = ("reader", "local-test-password")
        assert app.get("/api/reports/runtime").json()["items"] == []
        assert app.get("/api/reports/runtime?limit=101").status_code == 422
        assert app.get(f"/api/reports/runtime/{uuid4()}").json()["state"]["ai_enabled"] is False
        assert app.post("/api/reports/runtime").status_code == 405
