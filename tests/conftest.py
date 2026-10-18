import pytest


@pytest.fixture(autouse=True)
def isolate_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Developer configuration must not influence fixture-backed tests.
    for name in ("ATL_EXECUTION_MODE", "ATL_DATABASE_URL", "ATL_DATABASE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
