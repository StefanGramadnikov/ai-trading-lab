"""The dashboard API on PostgreSQL: sign-in, read models, working switches, acknowledgement."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.config import Settings
from ai_trading_lab.dashboard.app import create_dashboard_app
from ai_trading_lab.dashboard.auth import hash_password
from ai_trading_lab.live.config import LiveConfig
from ai_trading_lab.live.engine import LiveEngine
from ai_trading_lab.live.journal import ErrorJournal, error_groups, error_occurrences
from ai_trading_lab.live.store import (
    live_candles,
    live_decisions,
    live_equity,
    live_strategies,
    live_strategy_audit,
)
from ai_trading_lab.market_data.adapters.kraken import Candle

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PASSWORD = "a long enough test password"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
CONFIG = LiveConfig()
SECRET = "k" * 40


@pytest_asyncio.fixture
async def seeded(database_url: str) -> AsyncIterator[LiveEngine]:
    engine = create_async_engine(database_url)
    await _wipe(engine)
    live = LiveEngine(engine, lambda: AsyncMock(), CONFIG, clock=lambda: NOW)
    await live.setup()
    for minutes_ago, bump in ((70, "0"), (35, "1.5"), (5, "3.25")):
        at = NOW - timedelta(minutes=minutes_ago)
        for strategy_id in live.strategy_ids:
            await live.store.record_equity(
                strategy_id, at, Decimal("100") + Decimal(bump), Decimal("100"), Decimal(0)
            )
    await live.store.upsert_candles(
        "XBTEUR",
        [
            Candle(
                open_time_utc=NOW - timedelta(minutes=20),
                close_time_utc=NOW - timedelta(minutes=5),
                interval_minutes=15,
                open=Decimal("50000"),
                high=Decimal("50100"),
                low=Decimal("49900"),
                close=Decimal("50050"),
                vwap=Decimal("50000"),
                volume=Decimal("3"),
                trades=10,
            )
        ],
    )
    try:
        yield live
    finally:
        await _wipe(engine)
        await engine.dispose()


async def _wipe(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in (
            live_decisions,
            live_equity,
            live_strategy_audit,
            live_candles,
            error_occurrences,
            error_groups,
        ):
            await connection.execute(delete(table))
        await connection.execute(delete(live_strategies))


def settings(database_url: str, **extra: Any) -> Settings:
    return Settings(
        database_url=database_url,
        dashboard_password_hash=hash_password(PASSWORD),
        dashboard_session_secret=SECRET,
        **extra,
    )


class Browser:
    """A sign-in plus the CSRF header a page would send."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.csrf = ""

    def sign_in(self, password: str = PASSWORD) -> int:
        reply = self.client.post("/api/login", json={"password": password})
        if reply.status_code == 200:
            self.csrf = reply.json()["csrf"]
        return reply.status_code

    def post(self, path: str, body: dict[str, Any], **headers: str) -> Any:
        return self.client.post(path, json=body, headers={"x-csrf-token": self.csrf, **headers})


@pytest.fixture
def client(seeded: LiveEngine, database_url: str) -> Iterator[TestClient]:
    app = create_dashboard_app(settings(database_url), clock=lambda: NOW)
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


async def test_nothing_is_readable_before_signing_in(client: TestClient) -> None:
    for path in ("/api/overview", "/api/decisions", "/api/errors", "/api/audit", "/api/me"):
        reply = client.get(path)
        assert reply.status_code == 401, path
        assert reply.json()["error"] == "not_signed_in"
    assert client.get("/health/live").json()["execution_mode"] == "PAPER_ONLY"


async def test_a_wrong_password_is_refused_and_five_of_them_lock_the_door(
    client: TestClient,
) -> None:
    page = Browser(client)
    assert [page.sign_in("wrong password!") for _ in range(5)] == [401] * 5
    assert page.sign_in(PASSWORD) == 429  # even the right one waits out the lock
    assert client.get("/api/overview").status_code == 401


async def test_without_a_configured_password_every_sign_in_fails_closed(
    seeded: LiveEngine, database_url: str
) -> None:
    app = create_dashboard_app(
        Settings(database_url=database_url),
        clock=lambda: NOW,
    )
    with TestClient(app, raise_server_exceptions=False) as bare:
        reply = bare.post("/api/login", json={"password": PASSWORD})
        assert (reply.status_code, reply.json()["error"]) == (503, "dashboard_not_configured")
        assert bare.get("/api/overview").status_code == 503


async def test_the_overview_reports_strategies_curves_prices_and_a_young_run(
    client: TestClient,
) -> None:
    page = Browser(client)
    assert page.sign_in() == 200
    body = client.get("/api/overview").json()
    assert body["paper_only"] is True
    assert [s["kind"] for s in body["strategies"]] == ["rules", "benchmark"]  # AI is not set up
    rules = body["strategies"][0]
    assert (rules["equity"], rules["pnl_eur"], rules["pnl_pct"]) == ("103.25", "3.25", "3.25")
    assert rules["enabled"] is True and rules["start_eur"] == "100"
    assert len(body["curves"][rules["strategy_id"]]) == 3
    assert body["loop"] == {
        "healthy": True,
        "last_cycle_at": (NOW - timedelta(minutes=5)).isoformat(),
        "age_seconds": 300,
    }
    assert body["run"]["age_hours"] == 1.2
    assert body["verdict"]["state"] == "too_early"
    assert body["verdict"]["why"] == "run_under_72h"
    assert body["prices"]["XBTEUR"] == {"price": "50050", "age_seconds": 300}
    assert body["ai"]["state"] == "off"
    assert body["errors"]["unacknowledged_errors"] == 0
    # ETH and SOL have no prices yet: R3 fires for them, not for BTC.
    keys = {f["key"] for f in body["alerts"]["firing"]}
    assert keys == {"prices:ETHEUR", "prices:SOLEUR"}
    assert {r["id"] for r in body["alerts"]["rules"]} >= {"R1", "R2", "R3", "R4", "R5", "R6"}


async def test_a_stopped_loop_fires_r1(seeded: LiveEngine, database_url: str) -> None:
    later = NOW + timedelta(hours=2)
    app = create_dashboard_app(settings(database_url), clock=lambda: later)
    with TestClient(app, raise_server_exceptions=False) as stalled:
        assert Browser(stalled).sign_in() == 200
        body = stalled.get("/api/overview").json()
    assert body["loop"]["healthy"] is False
    assert "R1" in {f["rule"] for f in body["alerts"]["firing"]}


async def test_the_switch_works_and_is_audited_with_the_dashboard_as_actor(
    client: TestClient, seeded: LiveEngine
) -> None:
    page = Browser(client)
    page.sign_in()
    sid = seeded.rules_id
    reply = page.post(f"/api/strategies/{sid}/enabled", {"enabled": False, "reason": "testing"})
    assert reply.status_code == 200
    body = reply.json()
    assert body["changed"] is True
    assert (body["audit"]["actor"], body["audit"]["reason"]) == ("dashboard:owner", "testing")
    assert await seeded.store.is_enabled(sid) is False
    overview = client.get("/api/overview").json()
    assert overview["strategies"][0]["enabled"] is False
    assert overview["strategies"][0]["last_change"]["actor"] == "dashboard:owner"
    again = page.post(f"/api/strategies/{sid}/enabled", {"enabled": False, "reason": "again"})
    assert again.json()["changed"] is False
    assert client.get("/api/audit").json()["audit"][0]["action"] in {"disable", "enable"}


async def test_a_switch_needs_the_csrf_token_a_same_origin_and_a_reason(
    client: TestClient, seeded: LiveEngine
) -> None:
    page = Browser(client)
    page.sign_in()
    path = f"/api/strategies/{seeded.rules_id}/enabled"
    good = {"enabled": False, "reason": "why"}
    assert client.post(path, json=good).status_code == 403
    assert client.post(path, json=good, headers={"x-csrf-token": "nope"}).status_code == 403
    cross = page.post(path, good, origin="http://evil.example")
    assert (cross.status_code, cross.json()["error"]) == (403, "cross_origin")
    same = page.post(path, good, origin="http://testserver")
    assert same.status_code == 200
    assert page.post(path, {"enabled": True, "reason": "   "}).status_code == 422
    assert page.post(path, {"enabled": True, "reason": ""}).status_code == 422
    assert page.post(path, {"enabled": True, "reason": "x" * 201}).status_code == 422
    assert page.post(path, {"enabled": "maybe", "reason": "r"}).status_code == 422
    assert page.post(path, {"enabled": True, "reason": "r", "extra": 1}).status_code == 422
    assert (
        await seeded.store.is_enabled(seeded.rules_id) is False
    )  # only the one good call changed it
    assert page.post("/api/strategies/nobody/enabled", good).status_code == 404


async def test_a_switch_does_not_work_after_signing_out(
    client: TestClient, seeded: LiveEngine
) -> None:
    page = Browser(client)
    page.sign_in()
    client.post("/api/logout")
    reply = page.post(
        f"/api/strategies/{seeded.rules_id}/enabled", {"enabled": False, "reason": "late"}
    )
    assert reply.status_code == 401
    assert await seeded.store.is_enabled(seeded.rules_id) is True


async def test_an_idle_session_expires(seeded: LiveEngine, database_url: str) -> None:
    clock = {"t": 1_000_000.0}
    app = create_dashboard_app(
        settings(database_url, dashboard_idle_minutes=10),
        clock=lambda: NOW,
        wall=lambda: clock["t"],
    )
    with TestClient(app, raise_server_exceptions=False) as value:
        assert Browser(value).sign_in() == 200
        clock["t"] += 9 * 60
        assert value.get("/api/me").status_code == 200  # activity renews it
        clock["t"] += 9 * 60
        assert value.get("/api/me").status_code == 200
        clock["t"] += 11 * 60
        assert value.get("/api/me").status_code == 401


async def test_decisions_are_filterable_and_paged(client: TestClient, seeded: LiveEngine) -> None:
    for i in range(5):
        await seeded.store.log_decision(
            seeded.rules_id,
            "XBTEUR" if i % 2 == 0 else "ETHEUR",
            NOW - timedelta(minutes=15 * i),
            "hold",
            "no_breakout",
        )
    page = Browser(client)
    page.sign_in()
    first = client.get("/api/decisions", params={"limit": 2}).json()
    assert [d["reason"] for d in first["decisions"]] == ["no_breakout"] * 2
    assert first["next_before_seq"] is not None
    second = client.get(
        "/api/decisions", params={"limit": 2, "before_seq": first["next_before_seq"]}
    ).json()
    assert {d["seq"] for d in second["decisions"]}.isdisjoint(
        {d["seq"] for d in first["decisions"]}
    )
    btc = client.get("/api/decisions", params={"pair": "XBTEUR"}).json()
    assert {d["pair"] for d in btc["decisions"]} == {"XBTEUR"} and len(btc["decisions"]) == 3
    assert client.get("/api/decisions", params={"limit": 0}).status_code == 422
    assert client.get("/api/decisions", params={"pair": "xbt;drop"}).status_code == 422


async def test_errors_can_be_listed_inspected_and_acknowledged_from_the_page(
    client: TestClient, seeded: LiveEngine
) -> None:
    try:
        raise ValueError("api_key=SuperSecretValue123 broke")
    except ValueError as exc:
        await ErrorJournal(seeded.engine).record_exception(
            "ai_strategy", exc, strategy_id=seeded.rules_id, at=NOW
        )
    page = Browser(client)
    page.sign_in()
    listing = client.get("/api/errors").json()
    (problem,) = listing["problems"]
    assert listing["summary"]["unacknowledged_errors"] == 1
    assert "last_traceback" not in problem
    detail = client.get(f"/api/errors/{problem['fingerprint'][:12]}").json()
    assert "SuperSecretValue123" not in str(detail)
    assert "Traceback" in detail["problem"]["last_traceback"]
    assert "R6" in {f["rule"] for f in client.get("/api/overview").json()["alerts"]["firing"]}
    assert (
        page.post(f"/api/errors/{problem['fingerprint'][:12]}/ack", {"reason": ""}).status_code
        == 422
    )
    done = page.post(f"/api/errors/{problem['fingerprint'][:12]}/ack", {"reason": "known"})
    assert done.json() == {"acknowledged": 1}
    after = client.get(f"/api/errors/{problem['fingerprint']}").json()["problem"]
    assert (after["status"], after["acknowledged_by"]) == ("acknowledged", "dashboard:owner")
    assert client.get("/api/errors/zzzz").status_code == 404
    assert client.get("/api/errors/" + "a" * 64).status_code == 404


async def test_three_failing_cycles_of_one_strategy_fire_r2(
    client: TestClient, seeded: LiveEngine
) -> None:
    journal = ErrorJournal(seeded.engine)
    for i in range(3):
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            await journal.record_exception(
                "rules_strategy",
                exc,
                strategy_id=seeded.rules_id,
                at=NOW - timedelta(minutes=15 * i),
            )
    Browser(client).sign_in()
    firing = client.get("/api/overview").json()["alerts"]["firing"]
    assert any(f["rule"] == "R2" and f["strategy_id"] == seeded.rules_id for f in firing)


async def test_an_unexpected_failure_returns_a_plain_error_and_is_journaled(
    client: TestClient, seeded: LiveEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    Browser(client).sign_in()

    async def broken() -> dict[str, Any]:
        raise ZeroDivisionError("secret=hunter2hunter2")

    monkeypatch.setattr(client.app.state.data, "overview", broken)  # type: ignore[attr-defined]
    reply = client.get("/api/overview")
    assert (reply.status_code, reply.json()["error"]) == (500, "internal")
    assert "hunter2" not in reply.text
    (group,) = await ErrorJournal(seeded.engine).groups(level="error")
    assert group.component == "dashboard"
    assert "hunter2" not in (group.last_traceback or "") + (group.last_message or "")


async def test_a_database_outage_is_a_503_that_says_nothing_changed(
    client: TestClient, seeded: LiveEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = Browser(client)
    page.sign_in()

    async def down(*args: Any, **kwargs: Any) -> bool:
        raise OperationalError("select", {}, Exception("connection refused"))

    monkeypatch.setattr(client.app.state.data.store, "set_enabled", down)  # type: ignore[attr-defined]
    reply = page.post(
        f"/api/strategies/{seeded.rules_id}/enabled", {"enabled": False, "reason": "try"}
    )
    assert (reply.status_code, reply.json()["error"]) == (503, "database_unavailable")
    assert "Nothing was changed" in reply.json()["message"]
    assert await seeded.store.is_enabled(seeded.rules_id) is True


async def test_a_garbage_cookie_is_a_plain_401_and_creates_no_journal_entry(
    client: TestClient, seeded: LiveEngine
) -> None:
    for cookie in ("a.bé", "é.é", "x" * 600, "no-dot", "."):
        raw = f"atl_session={cookie}".encode("latin-1")
        reply = client.get("/api/me", headers=[(b"cookie", raw)])
        assert reply.status_code == 401, cookie
    assert await ErrorJournal(seeded.engine).groups() == []


async def test_unknown_routes_and_wrong_methods_still_carry_the_security_headers(
    client: TestClient,
) -> None:
    missing = client.get("/api/nothing-here")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert missing.headers["x-content-type-options"] == "nosniff"
    assert missing.json()["error"] == "Not Found"


async def test_parallel_wrong_passwords_cannot_all_get_past_the_throttle(
    client: TestClient,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(
            pool.map(
                lambda _: (
                    client.post("/api/login", json={"password": "wrong password!"}).status_code
                ),
                range(12),
            )
        )
    assert codes.count(401) == 5 and codes.count(429) == 7


async def test_a_write_renews_the_session_cookie(client: TestClient, seeded: LiveEngine) -> None:
    page = Browser(client)
    page.sign_in()
    reply = page.post(
        f"/api/strategies/{seeded.rules_id}/enabled", {"enabled": False, "reason": "renew"}
    )
    cookie = reply.headers["set-cookie"]
    assert cookie.startswith("atl_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie


async def test_the_sign_in_cookie_is_http_only_and_same_site_strict(client: TestClient) -> None:
    reply = client.post("/api/login", json={"password": PASSWORD})
    cookie = reply.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
