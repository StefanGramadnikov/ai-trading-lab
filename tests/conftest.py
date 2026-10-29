import os
import subprocess

import pytest


@pytest.fixture(autouse=True)
def isolate_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Developer configuration must not influence fixture-backed tests.
    for name in (
        "ATL_EXECUTION_MODE",
        "ATL_DATABASE_URL",
        "ATL_DATABASE_TIMEOUT_SECONDS",
        "ATL_REPORTING_USERNAME",
        "ATL_REPORTING_PASSWORD",
        "ATL_TELEGRAM_ENABLED",
        "ATL_TELEGRAM_BOT_TOKEN",
        "ATL_TELEGRAM_CHAT_ID",
        "ATL_AI_ENABLED",
        "ATL_AI_TOTAL_LIMIT_USD",
        "ATL_AI_REVIEWED_PRICES",
        "ATL_AI_MODEL",
        "ATL_OPENAI_API_KEY",
        "ATL_XAI_API_KEY",
        "ATL_ANTHROPIC_API_KEY",
        "ATL_DASHBOARD_PASSWORD_HASH",
        "ATL_DASHBOARD_SESSION_SECRET",
        "ATL_DASHBOARD_IDLE_MINUTES",
        "ATL_DEPLOYED_COMMIT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def database_url() -> str:
    from sqlalchemy.engine import make_url

    url = os.environ.get("ATL_TEST_DATABASE_URL")
    if not url:
        pytest.fail("ATL_TEST_DATABASE_URL is required; use an isolated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.database != "ai_trading_lab_test" or parsed.drivername != "postgresql+psycopg":
        pytest.fail(
            "Test database must be PostgreSQL named ai_trading_lab_test; refusing migrations"
        )
    env = {**os.environ, "ATL_DATABASE_URL": url, "ATL_EXECUTION_MODE": "PAPER_ONLY"}
    for arguments in [("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")]:
        subprocess.run(["alembic", *arguments], env=env, check=True, capture_output=True)
    return url
