from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_trading_lab.config import Settings


def test_safe_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.execution_mode == "PAPER_ONLY"
    assert settings.database_timeout_seconds == 2


@pytest.mark.parametrize("mode", ["LIVE", "paper", "", "PAPER_ONLY "])
def test_rejects_unsupported_execution_modes(mode: str) -> None:
    with pytest.raises(ValidationError):
        Settings(execution_mode=mode, _env_file=None)


@pytest.mark.parametrize("timeout", [0, -1, 31])
def test_rejects_invalid_timeout(timeout: int) -> None:
    with pytest.raises(ValidationError):
        Settings(database_timeout_seconds=timeout, _env_file=None)


def test_environment_and_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_EXECUTION_MODE", "LIVE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("ATL_EXECUTION_MODE", "PAPER_ONLY")
    monkeypatch.setenv("ATL_DATABASE_URL", "postgresql+psycopg://user:example@localhost/db")
    settings = Settings(_env_file=None)
    assert "example" not in repr(settings)
    assert settings.database_url.get_secret_value().endswith("/db")


@pytest.mark.parametrize("url", ["sqlite:///local.db", "https://example.com", ""])
def test_rejects_non_postgres_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=url, _env_file=None)


def test_env_file_and_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ATL_DATABASE_TIMEOUT_SECONDS=3\nATL_EXECUTION_MODE=PAPER_ONLY\n")
    assert Settings(_env_file=env_file).database_timeout_seconds == 3
    monkeypatch.setenv("ATL_DATABASE_TIMEOUT_SECONDS", "4")
    assert Settings(_env_file=env_file).database_timeout_seconds == 4


def test_invalid_database_url_is_redacted_in_error() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(database_url="postgresql://user:example_password@localhost/db", _env_file=None)
    assert "example_password" not in str(exc.value)
