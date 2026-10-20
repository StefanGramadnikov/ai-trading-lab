"""Real Chromium → authenticated FastAPI → isolated PostgreSQL paper evidence.

Deliberate browser-only response overrides cover unavailable/error and malicious
text states. The normal dashboard case uses the production API without overrides.
"""

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, Route, expect, sync_playwright
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from ai_trading_lab.app import create_app
from ai_trading_lab.config import Settings
from ai_trading_lab.experiments.store import ExperimentStore
from tests.experiment_helpers import experiment

pytestmark = pytest.mark.integration
PASSWORD = "local-browser-test-password"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    # Missing browser/dependency is a failed required gate, never a skipped test.
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture
def dashboard_server(database_url: str) -> Iterator[tuple[str, str]]:
    async def seed() -> str:
        engine = create_async_engine(database_url)
        try:
            spec = experiment()
            store = ExperimentStore(engine)
            at = datetime(2026, 10, 20, tzinfo=UTC)
            await store.create(spec, "create", at)
            await store.command(spec.experiment_id, "start", "start", at, "browser fixture")
            for cell in spec.cells:
                await store.advance(spec.experiment_id, cell.cell_id, at)
            return str(spec.experiment_id)
        finally:
            await engine.dispose()

    # Playwright owns an event loop on the pytest thread; DB seeding gets its
    # own worker loop rather than nesting asyncio.run inside that active loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        identity = executor.submit(lambda: asyncio.run(seed())).result(timeout=30)
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(database_url),
        reporting_username="browser-test",
        reporting_password=SecretStr(PASSWORD),
    )
    ready = threading.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets)
            ready.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = Server(uvicorn.Config(create_app(settings), log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            assert ready.wait(15), "Local browser test server failed to start"
            assert server.started
            yield f"http://127.0.0.1:{listener.getsockname()[1]}", identity
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive(), "Local browser test server did not stop"


def open_page(browser: Browser, base: str, *, mobile: bool = False, dark: bool = False) -> Page:
    context = browser.new_context(
        base_url=base,
        http_credentials={"username": "browser-test", "password": PASSWORD},
        viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 900},
        color_scheme="dark" if dark else "light",
    )
    return context.new_page()


@pytest.mark.parametrize(
    "mobile,dark", [(False, False), (False, True), (True, False), (True, True)]
)
def test_authenticated_paper_dashboard_and_export(
    browser: Browser,
    dashboard_server: tuple[str, str],
    tmp_path: Path,
    mobile: bool,
    dark: bool,
) -> None:
    base, identity = dashboard_server
    page = open_page(browser, base, mobile=mobile, dark=dark)
    methods: list[str] = []
    page.on("request", lambda request: methods.append(request.method))
    try:
        response = page.goto("/dashboard")
        assert response is not None and response.status == 200
        expect(page.get_by_text("PAPER ONLY · Read only", exact=True)).to_be_visible()
        expect(page.locator("#system-status")).to_contain_text("Database: available")
        # Navigate the real bounded list; previous tests can leave other cohorts.
        expect(page.locator("#list-status")).to_contain_text("Showing")
        button = page.locator("#experiment-list button").filter(has_text=identity)
        for _ in range(50):
            if button.count():
                break
            next_page = page.get_by_role("button", name="Next", exact=True)
            assert next_page.is_enabled(), "Seeded cohort missing from real experiment list"
            previous = page.locator("#list-status").inner_text()
            next_page.click()
            expect(page.locator("#list-status")).not_to_have_text(previous)
            expect(page.locator("#list-status")).to_contain_text("Showing")
        expect(button).to_be_visible()
        button.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#detail-status")).to_have_text(f"Experiment {identity}")
        expect(page.get_by_role("heading", name="rules · rules", exact=True)).to_be_visible()
        expect(
            page.get_by_text("Fixture AI (not a real provider call)", exact=False)
        ).to_be_visible()
        expect(page.get_by_text("No empirical performance claim.", exact=False)).to_be_visible()
        expect(page.get_by_role("table", name="rules exact NAV samples")).to_be_visible()
        expect(page.get_by_role("img", name="rules NAV overview", exact=False)).to_be_visible()
        api_report = page.context.request.get(f"/api/reports/experiments/{identity}").json()
        for article, cell in zip(
            page.locator("#report article").all(), api_report["cells"], strict=True
        ):
            for label, value in (
                ("NAV", cell["metrics"]["nav"]),
                ("Paid execution fees", cell["metrics"]["paid_fees"]),
                ("Unresolved open positions", cell["metrics"]["final_open_positions"]),
                ("Economic net P&L", cell["economic_net_pnl"]),
            ):
                row = article.get_by_role("row").filter(
                    has=page.get_by_role("cell", name=label, exact=True)
                )
                expect(row.get_by_role("cell").nth(1)).to_have_text(
                    "Unavailable" if value is None else str(value)
                )
        expect(page.locator("#detail-heading")).to_be_focused()
        with page.expect_download() as download:
            page.get_by_role("link", name="Download audit JSON").click()
        download_path = download.value.path()
        assert download_path is not None
        exported: Any = json.loads(Path(download_path).read_text())
        assert identity in json.dumps(exported)
        assert PASSWORD not in json.dumps(exported)
        assert set(methods) == {"GET"}
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(tmp_path / f"dashboard-{mobile}-{dark}.png"), full_page=True)
    finally:
        page.context.close()


def test_dashboard_assets_and_api_require_authentication(
    browser: Browser, dashboard_server: tuple[str, str]
) -> None:
    base, identity = dashboard_server
    context = browser.new_context(base_url=base)
    try:
        for path in (
            "/dashboard",
            "/dashboard/app.js",
            "/dashboard/style.css",
            "/api/reports/status",
            "/api/reports/experiments",
            f"/api/reports/experiments/{identity}",
            f"/api/reports/experiments/{identity}/export",
        ):
            response = context.request.get(path)
            assert response.status == 401
            assert response.headers["www-authenticate"].startswith("Basic")
            assert PASSWORD not in response.text()
    finally:
        context.close()


@pytest.mark.parametrize("status_code", [409, 503])
def test_dashboard_failure_clears_previous_results(
    browser: Browser, dashboard_server: tuple[str, str], status_code: int
) -> None:
    base, identity = dashboard_server
    page = open_page(browser, base)
    try:
        page.route(
            "**/api/reports/experiments?*",
            lambda route: route.fulfill(
                json={"items": [{"experiment_id": identity}], "limit": 20, "offset": 0}
            ),
        )
        page.goto("/dashboard")
        page.locator("#experiment-list button").click()
        expect(page.locator("#report article")).to_have_count(2)
        page.route(
            f"**/api/reports/experiments/{identity}",
            lambda route: route.fulfill(status=status_code, json={"detail": "sensitive internals"}),
        )
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.locator("#detail-status")).to_contain_text(
            "integrity checks" if status_code == 409 else "Database unavailable"
        )
        expect(page.locator("#report article")).to_have_count(0)
        expect(page.get_by_text("sensitive internals")).to_have_count(0)
    finally:
        page.context.close()


def test_dashboard_empty_missing_metrics_and_untrusted_text(
    browser: Browser, dashboard_server: tuple[str, str]
) -> None:
    base, identity = dashboard_server
    page = open_page(browser, base)
    attack = '<img src=x onerror="window.injected=true">'
    try:
        page.route(
            "**/api/reports/experiments?*",
            lambda route: route.fulfill(json={"items": [], "limit": 20, "offset": 0}),
        )
        page.goto("/dashboard")
        expect(page.locator("#list-status")).to_have_text("No experiments on this page.")
        expect(page.get_by_role("button", name="Next", exact=True)).to_be_disabled()
        page.unroute("**/api/reports/experiments?*")
        page.route(
            "**/api/reports/experiments?*",
            lambda route: route.fulfill(
                json={"items": [{"experiment_id": identity}], "limit": 20, "offset": 0}
            ),
        )

        def missing_report(route: Route) -> None:
            route.fulfill(
                json={
                    "experiment_id": identity,
                    "cells": [
                        {
                            "cell_id": attack,
                            "architecture": "rules",
                            "entry_suspended": True,
                            "economic_net_pnl": None,
                            "metrics": {"nav": None},
                            "equity_curve": [],
                            "unavailable_reasons": ["missing_cash_unit_fx"],
                        }
                    ],
                }
            )

        page.route(f"**/api/reports/experiments/{identity}", missing_report)
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.locator("#experiment-list button").click()
        expect(page.get_by_role("heading", name=f"{attack} · rules", exact=True)).to_be_visible()
        expect(page.get_by_text("NAV curve unavailable: no qualified marks.")).to_be_visible()
        expect(page.get_by_text("Risk / safety halt:", exact=False)).to_be_visible()
        expect(page.get_by_text("Unavailable reasons: missing_cash_unit_fx")).to_be_visible()
        expect(page.locator("#report img")).to_have_count(0)
        assert page.evaluate("window.injected === undefined")
        row = page.get_by_role("row").filter(has=page.get_by_role("cell", name="NAV", exact=True))
        expect(row).to_contain_text("Unavailable")
        expect(row).not_to_contain_text("0")
    finally:
        page.context.close()


def test_local_runtime_checkpoint_is_verified_and_read_only(
    browser: Browser, dashboard_server: tuple[str, str], database_url: str
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from ai_trading_lab.runtime.engine import reducer
    from ai_trading_lab.runtime.journal import Journal
    from ai_trading_lab.runtime.models import RuntimeConfig
    from tests.unit.test_runtime_engine import config

    async def seed_runtime() -> str:
        engine = create_async_engine(database_url)
        try:
            spec = RuntimeConfig.model_validate(config())
            await Journal(engine, reducer).create(
                spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc
            )
            return str(spec.session_id)
        finally:
            await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as pool:
        identity = pool.submit(asyncio.run, seed_runtime()).result(timeout=30)
    page = open_page(browser, dashboard_server[0])
    try:
        page.goto("/dashboard")
        expect(page.locator("#runtime-status")).to_contain_text("not a worker liveness probe")
        page.locator("#runtime-list button").filter(has_text=identity).click()
        expect(page.locator("#runtime-detail")).to_contain_text(identity)
        expect(page.locator("#runtime-detail")).to_contain_text('"paper_only": true')
        expect(page.locator("#runtime-detail")).to_contain_text('"ai_enabled": false')
        expect(page.get_by_role("button", name="Stop", exact=True)).to_have_count(0)
    finally:
        page.context.close()
