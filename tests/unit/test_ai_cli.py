"""CLI parsing, opt-in and credential redaction; no network or billed calls."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.ai import cli
from ai_trading_lab.ai.models import AIRequest, ProviderResult
from ai_trading_lab.ai.run_models import AIRunInput
from ai_trading_lab.config import Settings
from tests.ai_helpers import ai_spec


@pytest.fixture(autouse=True)
def ignore_local_activation_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer may enable real inference in .env while these tests run.
    # CLI tests must never inherit that activation or personal API key.
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None))


def write_input(tmp_path: Path) -> Path:
    path = tmp_path / "scenario.json"
    path.write_text(ai_spec().model_dump_json())
    return path


def test_bounded_json_and_modes(tmp_path: Path) -> None:
    path = write_input(tmp_path)
    assert cli.read_run(path).provider == "fixture"
    for invalid in (b"[]", b" " * 2_000_001, b"[" * 65 + b"0" + b"]" * 65, b"\xff"):
        path.write_bytes(invalid)
        with pytest.raises(ValueError):
            cli.read_run(path)
    spec = ai_spec()
    mutations: list[dict[str, object]] = [
        {"fixture_results": ()},
        {"run_spend_limit": Decimal(1)},
        {"inference_mode": "real"},
        {"price": spec.price.model_copy(update={"model": "different"})},
    ]
    for update in mutations:
        with pytest.raises(ValueError):
            AIRunInput.model_validate(spec.model_copy(update=update).model_dump(mode="json"))
    real = cli.read_run(Path("tests/fixtures/ai_openai_smoke.json"))
    assert real.provider == "openai" and real.budget.total_limit == 1
    sol = cli.read_run(Path("tests/fixtures/ai_openai_smoke_gpt_6_1_sol.json"))
    assert sol.model == sol.price.model == "gpt-6.1-sol" and sol.budget.total_limit == 1
    assert sol.budget.budget_id != real.budget.budget_id and sol.max_output_tokens == 2000
    for real_update in ({"purpose": "development"}, {"fixture_results": spec.fixture_results}):
        with pytest.raises(ValueError):
            AIRunInput.model_validate(real.model_copy(update=real_update).model_dump(mode="json"))


@pytest.mark.asyncio
async def test_doctor_does_not_echo_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATL_OPENAI_API_KEY", "synthetic-secret")
    assert await cli.run(cli.parser().parse_args(["doctor"])) == 0
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    assert json.loads(output)["openai_key_configured"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_fixture_run_cleanup(
    failure: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_input(tmp_path)
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.run.return_value = {"nav": None}
    if failure:
        store.run.side_effect = SQLAlchemyError("synthetic-secret")
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "AIRunStore", lambda actual, provider, **kwargs: store)
    args = cli.parser().parse_args(["run", str(path)])
    if failure:
        with pytest.raises(SQLAlchemyError):
            await cli.run(args)
    else:
        assert await cli.run(args) == 0
        assert json.loads(capsys.readouterr().out) == {"nav": None}
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_opt_in_missing_key_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    args = cli.parser().parse_args(["run", "tests/fixtures/ai_openai_smoke.json"])
    with pytest.raises(ValueError, match="disabled"):
        await cli.run(args)
    monkeypatch.setenv("ATL_AI_ENABLED", "true")
    with pytest.raises(ValueError, match="key"):
        await cli.run(args)
    monkeypatch.setenv("ATL_OPENAI_API_KEY", "synthetic-key")
    engine = AsyncMock(spec=AsyncEngine)
    store = AsyncMock()
    store.run.return_value = {}
    closed = []

    class FakeHTTP:
        def __init__(self, name: object, key: object) -> None:
            pass

        async def analyze(self, request: AIRequest) -> ProviderResult:
            raise AssertionError("network must not run in this test")

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cli, "HTTPProvider", FakeHTTP)
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cli, "AIRunStore", lambda actual, provider, **kwargs: store)
    assert await cli.run(args) == 0 and closed == [True]
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        ValueError("synthetic-secret"),
        OSError("synthetic-secret"),
        SQLAlchemyError("synthetic-secret"),
        TimeoutError(),
    ],
)
def test_main_status_and_sanitization(
    failure: Exception | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["atl-ai", "doctor"])
    monkeypatch.setattr(cli, "run", AsyncMock(return_value=0, side_effect=failure))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == (0 if failure is None else 2)
    assert "synthetic-secret" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_real_local_dotenv_cannot_activate_test_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = await asyncio.to_thread(Path("tests/fixtures/ai_openai_smoke.json").resolve)
    await asyncio.to_thread(
        (tmp_path / ".env").write_text,
        "ATL_AI_ENABLED=true\nATL_OPENAI_API_KEY=synthetic-local-only\n",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="disabled"):
        await cli.run(cli.parser().parse_args(["run", str(scenario)]))
