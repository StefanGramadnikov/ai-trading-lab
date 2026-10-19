"""Real PostgreSQL atomicity and recovery, using entirely synthetic research."""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid5

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.backtesting.store import ResearchStore, research_decisions, research_runs
from ai_trading_lab.paper_execution.store import PaperStore, portfolios
from tests.backtest_helpers import research

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_atomic_research_restart_concurrency_and_conflict(engine: AsyncEngine) -> None:
    spec = research()
    outputs = await asyncio.gather(ResearchStore(engine).run(spec), ResearchStore(engine).run(spec))
    assert outputs[0] == outputs[1]
    assert await ResearchStore(engine).run(spec) == outputs[0]
    assert await PaperStore(engine).reconcile(uuid5(spec.run_id, "rules-only"))
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(research_decisions)
                .where(research_decisions.c.run_id == spec.run_id)
            )
            == 1
        )
    for changed in (
        spec.model_copy(update={"initial_cash": Decimal(200)}),
        spec.model_copy(update={"code_version": "1" * 40}),
        spec.model_copy(update={"order_notional": Decimal(11)}),
    ):
        with pytest.raises(ValueError, match="immutable"):
            await ResearchStore(engine).run(changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["report", "input", "decision", "portfolio"])
async def test_research_integrity_failures_block_repeat(engine: AsyncEngine, damage: str) -> None:
    spec = research()
    await ResearchStore(engine).run(spec)
    async with engine.begin() as connection:
        if damage == "report":
            await connection.execute(
                research_runs.update()
                .where(research_runs.c.run_id == spec.run_id)
                .values(report={})
            )
        elif damage == "input":
            await connection.execute(
                research_runs.update().where(research_runs.c.run_id == spec.run_id).values(input={})
            )
        elif damage == "decision":
            await connection.execute(
                research_decisions.delete().where(research_decisions.c.run_id == spec.run_id)
            )
        else:
            await connection.execute(
                portfolios.update()
                .where(portfolios.c.portfolio_id == uuid5(spec.run_id, "rules-only"))
                .values(state={})
            )
    with pytest.raises(ValueError):
        await ResearchStore(engine).run(spec)


@pytest.mark.asyncio
async def test_research_write_failure_rolls_back_ledger_and_retry(engine: AsyncEngine) -> None:
    spec = research()

    def fail(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if statement.startswith("INSERT INTO research_decisions"):
            raise RuntimeError("injected research transaction failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await ResearchStore(engine).run(spec)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(research_runs)
                .where(research_runs.c.run_id == spec.run_id)
            )
            == 0
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(portfolios)
                .where(portfolios.c.portfolio_id == uuid5(spec.run_id, "rules-only"))
            )
            == 0
        )
    await ResearchStore(engine).run(spec)
    assert await PaperStore(engine).reconcile(uuid5(spec.run_id, "rules-only"))


@pytest.mark.asyncio
async def test_offline_cli_real_database_repeat(
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from ai_trading_lab.backtesting import cli

    spec = research()
    path = tmp_path / "research.json"
    await asyncio.to_thread(path.write_text, spec.model_dump_json())
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    args = cli.parser().parse_args(["run", str(path)])
    assert await cli.run(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert await cli.run(args) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert first["counts"]["fills"] == 1


@pytest.mark.asyncio
async def test_completed_research_portfolio_cannot_be_extended(engine: AsyncEngine) -> None:
    from ai_trading_lab.backtesting.runner import portfolio_config
    from ai_trading_lab.paper_execution.history import History
    from tests.integration.test_paper_store import intent

    spec = research()
    await ResearchStore(engine).run(spec)
    pid = uuid5(spec.run_id, "rules-only")
    store = PaperStore(engine)
    at = spec.end_at_utc
    with pytest.raises(ValueError, match="immutable"):
        await store.pause(pid, "post-run", True, at)
    with pytest.raises(ValueError, match="immutable"):
        await store.submit(intent(pid), spec.frames[0].quote, at)
    with pytest.raises(ValueError, match="immutable"):
        await store.settle(pid, "settle", spec.frames[0].quote.instrument, Decimal(1), False, at)
    # Simulate a coherent appended command through low-level owner access: all
    # M3 projections reconcile, but immutable research must still detect divergence.
    async with engine.begin() as connection:
        history, config = await store._load(connection, pid)
        assert config == portfolio_config(spec)
        assert isinstance(history, History)
        effect = history.apply("pause", "coherent-extra", {"paused": True}, at)
        await store._finish(connection, history, effect)
    assert await store.reconcile(pid)
    with pytest.raises(ValueError, match="configuration conflict"):
        await ResearchStore(engine).run(spec)


@pytest.mark.asyncio
async def test_strategy_version_cannot_be_rebound_to_different_parameters(
    engine: AsyncEngine,
) -> None:
    from uuid import uuid4

    spec = research()
    await ResearchStore(engine).run(spec)
    changed = spec.model_copy(
        update={
            "run_id": uuid4(),
            "strategy": spec.strategy.model_copy(update={"min_edge": Decimal("0.2")}),
        }
    )
    with pytest.raises(ValueError, match="Strategy version integrity"):
        await ResearchStore(engine).run(changed)
    # Changing the explicit version is the supported research revision path.
    changed = changed.model_copy(
        update={"strategy": changed.strategy.model_copy(update={"version": str(uuid4())})}
    )
    await ResearchStore(engine).run(changed)


@pytest.mark.asyncio
async def test_runtime_source_change_conflicts_with_stored_research(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trading_lab.backtesting import store
    from ai_trading_lab.backtesting.models import Evaluation
    from ai_trading_lab.backtesting.runner import evaluate

    spec = research()
    await ResearchStore(engine).run(spec)
    original = evaluate

    def changed_code(actual: Any) -> tuple[Evaluation, Any]:
        evaluation, history = original(actual)
        return evaluation.model_copy(update={"engine_hash": "f" * 64}), history

    monkeypatch.setattr(store, "evaluate", changed_code)
    with pytest.raises(ValueError, match="report integrity"):
        await ResearchStore(engine).run(spec)
