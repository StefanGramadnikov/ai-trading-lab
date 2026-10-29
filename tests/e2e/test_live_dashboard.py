"""Real Chromium against the V0 dashboard: sign in, read, switch, acknowledge."""

import asyncio
import socket
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

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

pytestmark = pytest.mark.integration
PASSWORD = "browser test password 123"
SECRET = "e" * 40


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        try:
            yield chromium
        finally:
            chromium.close()


async def _reset(database_url: str, *, seed: bool) -> dict[str, str]:
    engine = create_async_engine(database_url)
    try:
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
        if not seed:
            return {}
        now = datetime.now(UTC)
        live = LiveEngine(engine, lambda: AsyncMock(), LiveConfig(), clock=lambda: now)
        await live.setup()
        for index, minutes_ago in enumerate((90, 60, 30, 5)):
            for strategy_id in sorted(live.strategy_ids):
                bump = Decimal(index) * (
                    Decimal("0.7") if "rules" in strategy_id else Decimal("0.2")
                )
                await live.store.record_equity(
                    strategy_id,
                    now - timedelta(minutes=minutes_ago),
                    Decimal(100) + bump,
                    Decimal(100),
                    Decimal(0),
                )
        await live.store.log_decision(live.rules_id, "XBTEUR", now, "hold", "no_breakout")
        try:
            raise ValueError("token=abcdef1234567890abcdef broke <img src=x onerror=alert(1)>")
        except ValueError as exc:
            await ErrorJournal(engine).record_exception(
                "rules_strategy", exc, strategy_id=live.rules_id, at=now
            )
        return {"rules": live.rules_id}
    finally:
        await engine.dispose()


@pytest.fixture
def server(database_url: str) -> Iterator[tuple[str, dict[str, str]]]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        ids = executor.submit(lambda: asyncio.run(_reset(database_url, seed=True))).result(30)
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(database_url),
        dashboard_password_hash=SecretStr(hash_password(PASSWORD)),
        dashboard_session_secret=SecretStr(SECRET),
    )
    ready = threading.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets)
            ready.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        app = create_dashboard_app(settings)
        instance = Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=instance.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            assert ready.wait(15), "dashboard test server failed to start"
            yield f"http://127.0.0.1:{listener.getsockname()[1]}", ids
        finally:
            instance.should_exit = True
            thread.join(timeout=15)
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(lambda: asyncio.run(_reset(database_url, seed=False))).result(30)


def open_page(
    browser: Browser, base: str, *, mobile: bool = False, dark: bool = False
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        base_url=base,
        viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 900},
        color_scheme="dark" if dark else "light",
    )
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))

    def note(message: Any) -> None:
        # The page asks /api/me before sign-in; its 401 is the expected answer, not a fault.
        if message.type in ("error", "warning") and "status of 401" not in message.text:
            problems.append(f"console: {message.text}")

    page.on("console", note)
    return page, problems


def sign_in(page: Page, password: str = PASSWORD) -> None:
    page.goto("/")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()


@pytest.mark.parametrize("mobile,dark", [(False, False), (False, True), (True, False)])
def test_sign_in_then_read_the_overview_without_browser_errors(
    browser: Browser, server: tuple[str, dict[str, str]], mobile: bool, dark: bool
) -> None:
    base, _ = server
    page, problems = open_page(browser, base, mobile=mobile, dark=dark)
    try:
        page.goto("/")
        expect(page.locator(".paper")).to_contain_text("PAPER MONEY ONLY")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        sign_in(page, "wrong password here")
        expect(page.locator("#pw-err")).to_contain_text("Wrong password")
        sign_in(page)
        expect(page.get_by_role("heading", name="Needs your attention")).to_be_visible()
        expect(page.get_by_text("Too early to tell").first).to_be_visible()
        expect(page.get_by_role("switch").first).to_be_visible()
        # The mobile layout must never scroll sideways.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert problems == []
    finally:
        page.context.close()


def test_the_switch_confirms_requires_a_reason_and_is_audited(
    browser: Browser, server: tuple[str, dict[str, str]]
) -> None:
    base, ids = server
    page, problems = open_page(browser, base)
    try:
        sign_in(page)
        switch = page.get_by_role("switch", name="Rules breakout on or off")
        expect(switch).to_have_attribute("aria-checked", "true")
        switch.click()
        dialog = page.locator("dialog")
        expect(dialog).to_contain_text("wind-down")
        dialog.get_by_role("button", name="Switch off").click()  # no reason yet
        expect(dialog).to_be_visible()
        expect(page.get_by_role("switch", name="Rules breakout on or off")).to_have_attribute(
            "aria-checked", "true"
        )
        dialog.get_by_label("Reason (required)").fill("e2e test")
        dialog.get_by_role("button", name="Switch off").click()
        expect(page.locator("#toast")).to_contain_text("dashboard:owner")
        expect(page.get_by_role("switch", name="Rules breakout on or off")).to_have_attribute(
            "aria-checked", "false"
        )
        page.reload()
        expect(page.get_by_role("switch", name="Rules breakout on or off")).to_have_attribute(
            "aria-checked", "false"
        )
        assert problems == []
    finally:
        page.context.close()
    assert ids["rules"]


def test_an_error_is_shown_as_text_and_can_be_acknowledged(
    browser: Browser, server: tuple[str, dict[str, str]]
) -> None:
    base, _ = server
    page, problems = open_page(browser, base)
    try:
        sign_in(page)
        page.get_by_role("link", name="Errors").click()
        card = page.locator("article.err").first
        expect(card).to_be_visible()
        card.get_by_role("button", name="Show detail").click()
        expect(card).to_contain_text("secrets redacted")
        assert page.locator("article.err img").count() == 0  # hostile text stays text
        assert "abcdef1234567890abcdef" not in page.content()
        card.get_by_role("button", name="Acknowledge").click()
        dialog = page.locator("dialog")
        dialog.get_by_role("button", name="Acknowledge").click()
        expect(page.locator("#toast")).to_contain_text("Acknowledged")
        assert problems == []
    finally:
        page.context.close()


def test_signing_out_returns_to_the_sign_in_screen(
    browser: Browser, server: tuple[str, dict[str, str]]
) -> None:
    base, _ = server
    page, _problems = open_page(browser, base)
    try:
        sign_in(page)
        expect(page.get_by_role("heading", name="Needs your attention")).to_be_visible()
        page.get_by_role("button", name="Sign out").click()
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        response: Any = page.request.get(base + "/api/overview")
        assert response.status == 401
    finally:
        page.context.close()
