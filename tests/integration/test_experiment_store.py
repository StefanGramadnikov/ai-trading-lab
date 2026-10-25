"""Real PostgreSQL lifecycle, command replay, integrity and atomic recovery."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.experiments.store import ExperimentStore, experiment_events, experiments
from tests.experiment_helpers import experiment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
AT = datetime(2026, 10, 20, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_lifecycle_replays_original_snapshots_and_config(engine: AsyncEngine) -> None:
    spec = experiment()
    store = ExperimentStore(engine)
    created = await store.create(spec, "create", AT)
    assert created["state"] == "CREATED"
    running = await store.command(spec.experiment_id, "start", "start", AT, "research")
    assert running["state"] == "RUNNING"
    assert await store.create(spec, "create", AT) == created
    assert await store.command(spec.experiment_id, "start", "start", AT, "research") == running
    with pytest.raises(ValueError, match="conflict"):
        await store.command(spec.experiment_id, "start", "start", AT, "changed")
    with pytest.raises(ValueError, match="immutable"):
        await store.create(spec.model_copy(update={"version": "changed"}), "create", AT)
    with pytest.raises(ValueError, match="transition"):
        await store.command(spec.experiment_id, "approve", "approve", AT, "premature")
    with pytest.raises(ValueError, match="regression"):
        await store.command(spec.experiment_id, "pause", "pause", AT - timedelta(seconds=1), "")
    paused = await store.command(spec.experiment_id, "pause", "pause", AT, "manual")
    assert paused["state"] == "PAUSED" and paused["resume_state"] == "RUNNING"
    with pytest.raises(ValueError, match="RUNNING"):
        await store.advance(spec.experiment_id, "blocked", AT)
    assert (await store.command(spec.experiment_id, "resume", "resume", AT, ""))[
        "state"
    ] == "RUNNING"


async def test_atomic_cells_concurrent_advance_and_restart(engine: AsyncEngine) -> None:
    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    a, b = await asyncio.gather(*(store.advance(spec.experiment_id, "first", AT) for _ in range(2)))
    assert a == b and a["completed_cells"] == 1
    second = await store.advance(spec.experiment_id, "second", AT)
    assert second["completed_cells"] == 2
    assert second["state"] in ("EVALUATING", "PAUSED")
    assert await ExperimentStore(engine).advance(spec.experiment_id, "first", AT) == a
    report: Any = await store.report(spec.experiment_id)
    assert report["lifecycle"] == second
    if second["state"] == "PAUSED":
        await store.command(spec.experiment_id, "resume", "resume", AT, "")
    rejected = await store.command(
        spec.experiment_id, "reject", "reject", AT, "not evidence of an edge"
    )
    assert rejected["state"] == "REJECTED"
    with pytest.raises(ValueError, match="transition"):
        await store.command(spec.experiment_id, "restart", "start", AT, "")


async def test_rollback_retains_start_checkpoint(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trading_lab.experiments import runner

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    original = runner.evaluate_cell
    monkeypatch.setattr(
        runner, "evaluate_cell", AsyncMock(side_effect=ValueError("synthetic interruption"))
    )
    with pytest.raises(ValueError, match="interruption"):
        await store.advance(spec.experiment_id, "advance", AT)
    async with engine.connect() as connection:
        row: Any = (
            (
                await connection.execute(
                    select(experiments).where(experiments.c.experiment_id == spec.experiment_id)
                )
            )
            .mappings()
            .one()
        )
        assert row["checkpoint"]["reports"] == []
        assert row["checkpoint"]["state"] == "RUNNING"
    monkeypatch.setattr(runner, "evaluate_cell", original)
    assert (await store.advance(spec.experiment_id, "advance", AT))["completed_cells"] == 1


@pytest.mark.parametrize("kind", ["event_delete", "event_edit", "checkpoint", "runtime", "config"])
async def test_tampering_fails_closed(engine: AsyncEngine, kind: str) -> None:
    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    async with engine.begin() as connection:
        if kind == "event_delete":
            await connection.execute(
                delete(experiment_events).where(
                    experiment_events.c.experiment_id == spec.experiment_id,
                    experiment_events.c.sequence == 2,
                )
            )
        elif kind == "event_edit":
            await connection.execute(
                update(experiment_events)
                .where(experiment_events.c.experiment_id == spec.experiment_id)
                .values(payload={"action": "invented"})
            )
        else:
            value = {"checkpoint": {}, "runtime": "0" * 64, "config": "0" * 64}[kind]
            column = {
                "checkpoint": "checkpoint",
                "runtime": "engine_hash",
                "config": "config_hash",
            }[kind]
            await connection.execute(
                update(experiments)
                .where(experiments.c.experiment_id == spec.experiment_id)
                .values(**{column: value})
            )
    with pytest.raises((ValueError, KeyError), match="integrity|sequence"):
        await store.report(spec.experiment_id)


async def test_invalid_boundary_inputs(engine: AsyncEngine) -> None:
    store = ExperimentStore(engine)
    with pytest.raises(ValueError, match="unknown"):
        await store.report(uuid4())
    with pytest.raises(ValueError, match="timezone"):
        await store.create(experiment(), "create", AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="bounded"):
        await store.create(experiment(), "", AT)


async def test_completed_approval_requires_sufficient_evidence(engine: AsyncEngine) -> None:
    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "first", AT)
    await store.advance(spec.experiment_id, "second", AT)
    report: Any = await store.report(spec.experiment_id)
    if report["lifecycle"]["state"] == "PAUSED":
        await store.command(spec.experiment_id, "resume", "resume", AT, "")
    assert report["evidence_sufficient"] is True
    approved = await store.command(
        spec.experiment_id, "approve", "approve", AT, "fixture evaluation only"
    )
    assert approved["state"] == "APPROVED"
    assert (
        await store.command(spec.experiment_id, "approve", "approve", AT, "fixture evaluation only")
        == approved
    )


async def test_paper_projection_tamper_blocks_replay(engine: AsyncEngine) -> None:
    from uuid import uuid5

    from ai_trading_lab.paper_execution.store import portfolios

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "first", AT)
    async with engine.begin() as connection:
        await connection.execute(
            update(portfolios)
            .where(portfolios.c.portfolio_id == uuid5(spec.experiment_id, "cell:rules"))
            .values(state={})
        )
    with pytest.raises(ValueError, match="reconciliation"):
        # PaperStore verifies the ledger before the experiment projection digest.
        await store.advance(spec.experiment_id, "first", AT)


async def test_unknown_command_and_changed_timestamp_refused(engine: AsyncEngine) -> None:
    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    with pytest.raises(ValueError, match="unsupported"):
        await store.command(spec.experiment_id, "finish", "finish", AT, "")
    with pytest.raises(ValueError, match="conflict"):
        await store.command(
            spec.experiment_id, "start", "start", AT + timedelta(microseconds=1), ""
        )
    # ISO strings with fractional seconds must be compared as instants, not text.
    await store.command(spec.experiment_id, "pause", "pause", AT + timedelta(microseconds=1), "")
    with pytest.raises(ValueError, match="regression"):
        await store.command(spec.experiment_id, "resume", "resume", AT, "")


async def test_incomplete_expenses_block_approval(engine: AsyncEngine) -> None:
    spec = experiment().model_copy(update={"expenses_complete": False})
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "first", AT)
    state = await store.advance(spec.experiment_id, "second", AT)
    if state["state"] == "PAUSED":
        await store.command(spec.experiment_id, "resume", "resume", AT, "")
    with pytest.raises(ValueError, match="insufficient"):
        await store.command(spec.experiment_id, "approve", "approve", AT, "")
    current: Any = await store.report(spec.experiment_id)
    assert current["lifecycle"]["state"] == "EVALUATING"


async def test_ai_checkpoint_survives_lost_final_transaction(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trading_lab.ai.budget import BudgetStore

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "first", AT)
    original = store._append
    monkeypatch.setattr(store, "_append", AsyncMock(side_effect=ValueError("lost cell commit")))
    with pytest.raises(ValueError, match="lost cell"):
        await store.advance(spec.experiment_id, "second", AT)
    ai = spec.cells[1].ai
    assert ai is not None
    before = await BudgetStore(engine).summary(ai.budget.budget_id)
    assert before["call_count"] == 1
    with pytest.raises(ValueError, match="immutable"):
        await store.create(
            spec.model_copy(update={"version": "changed-after-inference"}), "create", AT
        )
    monkeypatch.setattr(store, "_append", original)
    after = await store.advance(spec.experiment_id, "second", AT)
    assert after["completed_cells"] == 2
    assert await BudgetStore(engine).summary(ai.budget.budget_id) == before


@pytest.mark.parametrize("uncertain", [False, True])
async def test_verified_call_costs_and_unknown_inference_latches_pause(
    engine: AsyncEngine, uncertain: bool
) -> None:
    from decimal import Decimal

    from ai_trading_lab.ai.budget import BudgetStore, ProviderLimit
    from ai_trading_lab.ai.models import ProviderResult
    from ai_trading_lab.experiments.models import ExperimentConfig

    spec = experiment()
    ai = spec.cells[1].ai
    assert ai is not None
    first = ai.research.frames[0]
    later_at = first.at_utc + timedelta(seconds=1)
    later = first.model_copy(
        update={
            "at_utc": later_at,
            "quote": first.quote.model_copy(
                update={
                    "snapshot_id": uuid4(),
                    "event_time_utc": later_at,
                    "received_at_utc": later_at,
                }
            ),
        }
    )
    tape = ai.research.model_copy(update={"frames": (first, later)})
    ai = ai.model_copy(update={"research": tape, "fixture_results": ai.fixture_results * 2})
    if uncertain:
        ai = ai.model_copy(
            update={
                "fixture_results": (
                    ProviderResult(status="unknown", model=ai.model, reason="lost_response"),
                ),
                "price": ai.price.model_copy(
                    update={"input_per_million": Decimal(1), "output_per_million": Decimal(2)}
                ),
                "budget": ai.budget.model_copy(
                    update={
                        "total_limit": Decimal(1),
                        "daily_limit": Decimal(1),
                        "monthly_limit": Decimal(1),
                        "provider_limits": (ProviderLimit(provider="fixture", limit="1"),),
                    }
                ),
                "run_spend_limit": Decimal(1),
            }
        )
    cells = (
        spec.cells[0].model_copy(update={"rules": tape.model_copy(update={"run_id": uuid4()})}),
        spec.cells[1].model_copy(update={"ai": ai}),
    )
    spec = ExperimentConfig.model_validate(
        spec.model_copy(update={"cells": cells}).model_dump(mode="json")
    )
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "rules", AT)
    state = await store.advance(spec.experiment_id, "hybrid", AT)
    costs = await BudgetStore(engine).costs_for_run(ai.budget.budget_id, ai.research.run_id)
    report: Any = await store.report(spec.experiment_id)
    hybrid = next(cell for cell in report["cells"] if cell["cell_id"] == "hybrid")
    if uncertain:
        assert len(costs) == 1
        assert costs[0].amount is None and costs[0].held == Decimal("0.018")
        assert state["state"] == "PAUSED" and state["resume_state"] == "EVALUATING"
        assert hybrid["economic_net_pnl"] is None
        assert hybrid["entry_suspended"] is True
        assert hybrid["ai_metrics"]["recorded_calls"] == 1
        assert hybrid["ai_metrics"]["input_tokens"] is None
        assert report["evidence_sufficient"] is False
        summary = await BudgetStore(engine).summary(ai.budget.budget_id)
        assert summary["reserved"] == "0.018" and summary["unresolved"] == 1
        assert await BudgetStore(engine).costs_for_run(ai.budget.budget_id, uuid4()) == ()
        # Both frames qualify, but uncertainty latches suspension before frame two.
        assert hybrid["metrics"]["counts"]["decisions"] == 2
    else:
        assert costs and all(cost.amount == 0 and cost.held == 0 for cost in costs)
        assert all(cost.currency == "USD" and cost.provider == "fixture" for cost in costs)
        assert hybrid["ai_metrics"]["recorded_calls"] == len(costs)


async def test_four_architecture_example_has_independent_bounded_calls_and_conserved_costs(
    engine: AsyncEngine,
) -> None:
    from decimal import Decimal
    from pathlib import Path

    from ai_trading_lab.ai.budget import BudgetStore
    from ai_trading_lab.experiments.models import ExperimentConfig

    spec = ExperimentConfig.model_validate_json(
        await asyncio.to_thread(Path("tests/fixtures/experiment_architecture.json").read_text)
    )
    spec = spec.model_copy(update={"experiment_id": uuid4()})
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    for cell in spec.cells:
        state = await store.advance(spec.experiment_id, cell.cell_id, AT)
        assert state["state"] != "PAUSED"
    report: Any = await store.report(spec.experiment_id)
    assert state["state"] == "EVALUATING" and state["completed_cells"] == 4
    assert report["evidence_sufficient"] is True
    assert [cell["ai_metrics"]["recorded_calls"] for cell in report["cells"]] == [0, 1, 3, 6]
    for cell, expected in zip(spec.cells[1:], (1, 3, 6), strict=True):
        assert cell.ai is not None
        assert (
            len(
                await BudgetStore(engine).costs_for_run(
                    cell.ai.budget.budget_id, cell.research.run_id
                )
            )
            == expected
        )
    total = sum((Decimal(cell["costs"]["total_cost"]) for cell in report["cells"]), Decimal(0))
    assert total == Decimal(report["costs"]["consolidated"]["total_cost"])
    assert len(report["paired_comparisons"]) == 3
    assert await store.report(spec.experiment_id) == report


async def test_cloned_experiment_cannot_reuse_inner_run_ids_or_costs(engine: AsyncEngine) -> None:
    from ai_trading_lab.ai.budget import BudgetStore

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "rules", AT)
    await store.advance(spec.experiment_id, "hybrid", AT)
    ai = spec.cells[1].ai
    assert ai is not None
    original = await BudgetStore(engine).costs_for_run(ai.budget.budget_id, ai.research.run_id)
    clone = spec.model_copy(update={"experiment_id": uuid4()})
    with pytest.raises(ValueError):
        await store.create(clone, "create", AT)
    assert (
        await BudgetStore(engine).costs_for_run(ai.budget.budget_id, ai.research.run_id) == original
    )
    report: Any = await store.report(spec.experiment_id)
    assert report["cells"][1]["ai_metrics"]["recorded_calls"] == len(original) == 1
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(experiments.c.experiment_id).where(
                    experiments.c.experiment_id == clone.experiment_id
                )
            )
            is None
        )


async def test_concurrent_experiment_creation_has_one_run_owner(engine: AsyncEngine) -> None:
    from ai_trading_lab.run_identity import run_owners

    first = experiment()
    second = first.model_copy(update={"experiment_id": uuid4()})
    store = ExperimentStore(engine)
    results = await asyncio.gather(
        store.create(first, "create", AT),
        store.create(second, "create", AT),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ValueError) for item in results) == 1
    assert sum(isinstance(item, dict) for item in results) == 1
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(run_owners).where(
                        run_owners.c.run_id.in_([cell.research.run_id for cell in first.cells])
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2 and len({row["owner_id"] for row in rows}) == 1
        assert {row["owner_kind"] for row in rows} == {"experiment"}


@pytest.mark.parametrize("tamper", ["delete", "owner", "config"])
async def test_missing_or_changed_run_owner_blocks_replay(engine: AsyncEngine, tamper: str) -> None:
    from ai_trading_lab.run_identity import run_owners

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    async with engine.begin() as connection:
        identity = run_owners.c.run_id == spec.cells[0].research.run_id
        if tamper == "delete":
            await connection.execute(delete(run_owners).where(identity))
        else:
            changes: dict[str, object] = (
                {"owner_id": uuid4()} if tamper == "owner" else {"config_hash": "0" * 64}
            )
            await connection.execute(update(run_owners).where(identity).values(**changes))
    with pytest.raises(ValueError, match="ownership"):
        await store.create(spec, "create", AT)
    with pytest.raises(ValueError, match="ownership"):
        await store.report(spec.experiment_id)


async def test_existing_m5_run_cannot_be_reowned_by_experiment(engine: AsyncEngine) -> None:
    from ai_trading_lab.ai.budget import BudgetStore
    from ai_trading_lab.ai.providers import FixtureProvider
    from ai_trading_lab.ai.run_store import AIRunStore
    from ai_trading_lab.run_identity import run_owners

    spec = experiment()
    ai = spec.cells[1].ai
    assert ai is not None
    before = await AIRunStore(engine, FixtureProvider(ai.fixture_results)).run(ai)
    costs = await BudgetStore(engine).costs_for_run(ai.budget.budget_id, ai.research.run_id)
    with pytest.raises(ValueError):
        await ExperimentStore(engine).create(spec, "create", AT)
    assert await AIRunStore(engine, FixtureProvider(())).run(ai) == before
    assert await BudgetStore(engine).costs_for_run(ai.budget.budget_id, ai.research.run_id) == costs
    async with engine.connect() as connection:
        # The failed experiment transaction must not retain a partial rules claim.
        assert (
            await connection.scalar(
                select(run_owners.c.run_id).where(
                    run_owners.c.run_id == spec.cells[0].research.run_id
                )
            )
            is None
        )


async def test_experiment_owner_blocks_other_public_runners(engine: AsyncEngine) -> None:
    from ai_trading_lab.ai.providers import FixtureProvider
    from ai_trading_lab.ai.run_store import AIRunStore
    from ai_trading_lab.backtesting.store import ResearchStore

    spec = experiment()
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    with pytest.raises(ValueError, match="owned"):
        await ResearchStore(engine).run(spec.cells[0].research)
    ai = spec.cells[1].ai
    assert ai is not None
    with pytest.raises(ValueError, match="owned"):
        await AIRunStore(engine, FixtureProvider(ai.fixture_results)).run(ai)
    assert (await store.report(spec.experiment_id))["evidence_sufficient"] is False


async def test_legacy_run_records_cannot_be_claimed_by_new_cohort(engine: AsyncEngine) -> None:
    from ai_trading_lab.backtesting.store import ResearchStore
    from ai_trading_lab.run_identity import run_owners

    spec = experiment()
    await ResearchStore(engine).run(spec.cells[0].research)
    async with engine.begin() as connection:
        await connection.execute(
            delete(run_owners).where(run_owners.c.run_id == spec.cells[0].research.run_id)
        )
    with pytest.raises(ValueError, match="legacy research"):
        await ExperimentStore(engine).create(spec, "create", AT)


async def test_stale_endpoint_pauses_and_blocks_approval(engine: AsyncEngine) -> None:
    spec = experiment()
    cells = []
    for cell in spec.cells:
        research = cell.research.model_copy(
            update={"end_at_utc": cell.research.end_at_utc + timedelta(days=1)}
        )
        if cell.rules is not None:
            cells.append(cell.model_copy(update={"rules": research}))
        else:
            assert cell.ai is not None
            cells.append(
                cell.model_copy(update={"ai": cell.ai.model_copy(update={"research": research})})
            )
    spec = spec.model_copy(update={"cells": tuple(cells)})
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    assert (await store.advance(spec.experiment_id, "first", AT))["state"] == "PAUSED"
    await store.command(spec.experiment_id, "resume1", "resume", AT, "")
    await store.advance(spec.experiment_id, "second", AT)
    await store.command(spec.experiment_id, "resume2", "resume", AT, "")
    with pytest.raises(ValueError, match="insufficient"):
        await store.command(spec.experiment_id, "approval", "approve", AT, "")
    output: Any = await store.report(spec.experiment_id)
    assert output["evidence_sufficient"] is False
    assert all(cell["equity_curve"][-1]["nav"] is None for cell in output["cells"])


async def test_deleted_experiment_owner_cannot_be_appropriated_by_m4(engine: AsyncEngine) -> None:
    from ai_trading_lab.backtesting.store import ResearchStore
    from ai_trading_lab.run_identity import run_owners

    spec = experiment()
    await ExperimentStore(engine).create(spec, "create", AT)
    async with engine.begin() as connection:
        await connection.execute(
            delete(run_owners).where(run_owners.c.run_id == spec.cells[0].research.run_id)
        )
    with pytest.raises(ValueError, match="legacy experiment"):
        await ResearchStore(engine).run(spec.cells[0].research)


async def test_legacy_ai_report_without_calls_or_start_keeps_identity(engine: AsyncEngine) -> None:
    from decimal import Decimal

    from ai_trading_lab.ai.budget import BudgetStore
    from ai_trading_lab.ai.providers import FixtureProvider
    from ai_trading_lab.ai.run_store import AIRunStore, ai_run_starts, ai_runs
    from ai_trading_lab.backtesting.store import ResearchStore
    from ai_trading_lab.run_identity import run_owners

    spec = experiment()
    ai = spec.cells[1].ai
    assert ai is not None
    frame = ai.research.frames[0]
    assert frame.forecast is not None
    frame = frame.model_copy(
        update={"forecast": frame.forecast.model_copy(update={"probability": Decimal("0.5")})}
    )
    legacy = ai.model_copy(update={"research": ai.research.model_copy(update={"frames": (frame,)})})
    await AIRunStore(engine, FixtureProvider(())).run(legacy)
    assert (await BudgetStore(engine).summary(ai.budget.budget_id))["call_count"] == 0
    async with engine.begin() as connection:
        await connection.execute(
            delete(run_owners).where(run_owners.c.run_id == ai.research.run_id)
        )
        await connection.execute(
            delete(ai_run_starts).where(ai_run_starts.c.run_id == ai.research.run_id)
        )
        await connection.execute(
            update(ai_runs).where(ai_runs.c.run_id == ai.research.run_id).values(report={})
        )
    with pytest.raises(ValueError, match="legacy AI"):
        await ExperimentStore(engine).create(spec, "create", AT)
    with pytest.raises(ValueError, match="legacy AI"):
        await ResearchStore(engine).run(ai.research)


async def test_f13_append_invoice_and_completion_are_durable_and_immutable(
    engine: AsyncEngine,
) -> None:
    from decimal import Decimal

    from ai_trading_lab.experiments.costs import Expense

    spec = experiment().model_copy(update={"expenses_complete": False})
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    await store.advance(spec.experiment_id, "first", AT)
    await store.advance(spec.experiment_id, "second", AT)
    report: Any = await store.report(spec.experiment_id)
    estimate = report["costs"]["expenses"][0]
    invoice = Expense.model_validate(
        estimate
        | {
            "expense_id": "invoice-observed",
            "kind": "actual",
            "amount": "0.02",
            "evidence": "observed",
            "source": "operator-supplied-invoice-reference",
            "replaces_expense_id": estimate["expense_id"],
        }
    )
    attached = await store.attach_expense(spec.experiment_id, "invoice", AT, invoice)
    restarted = ExperimentStore(engine)
    assert await restarted.attach_expense(spec.experiment_id, "invoice", AT, invoice) == attached
    report = await restarted.report(spec.experiment_id)
    assert report["configuration"] == spec.model_dump(mode="json")
    assert report["config_hash"] == spec.fingerprint
    assert report["expenses_complete"] is False
    assert report["empirical_performance_claim"] is False
    assert report["costs"]["excluded_estimate_ids"] == [estimate["expense_id"]]
    assert Decimal(report["costs"]["consolidated"]["actual_cost"]) == Decimal("0.02")
    assert Decimal(report["costs"]["consolidated"]["total_cost"]) == Decimal("0.02")
    with pytest.raises(ValueError, match="Duplicate expense"):
        await restarted.attach_expense(spec.experiment_id, "duplicate", AT, invoice)
    with pytest.raises(ValueError, match="conflict"):
        await restarted.attach_expense(
            spec.experiment_id, "invoice", AT, invoice.model_copy(update={"amount": Decimal(1)})
        )
    complete = await restarted.complete_expenses(
        spec.experiment_id, "complete", AT, "all bills checked"
    )
    assert (
        await ExperimentStore(engine).complete_expenses(
            spec.experiment_id, "complete", AT, "all bills checked"
        )
        == complete
    )
    assert (await restarted.report(spec.experiment_id))["expenses_complete"] is True
    async with engine.connect() as connection:
        stored = await connection.scalar(
            select(experiments.c.config).where(experiments.c.experiment_id == spec.experiment_id)
        )
    assert stored == spec.model_dump(mode="json")


async def test_f13_completion_guards_and_post_completion_attach_refused(
    engine: AsyncEngine,
) -> None:
    from ai_trading_lab.experiments.costs import CostAllocation, Expense

    spec = experiment().model_copy(update={"expenses_complete": False})
    store = ExperimentStore(engine)
    await store.create(spec, "create", AT)
    await store.command(spec.experiment_id, "start", "start", AT, "")
    invoice = Expense(
        expense_id="hosting-1",
        cost_key="host-month",
        category="hosting",
        kind="actual",
        amount="1",
        currency="USD",
        incurred_at_utc=AT,
        source="operator-supplied-invoice-reference",
        evidence="observed",
        allocations=(CostAllocation(cell_id="rules", weight="1"),),
    )
    # Completeness cannot be declared while cells are still outstanding, or with no reason.
    with pytest.raises(ValueError, match="all cells must report"):
        await store.complete_expenses(spec.experiment_id, "early", AT, "too soon")
    with pytest.raises(ValueError, match="reason required"):
        await store.complete_expenses(spec.experiment_id, "blank", AT, " ")
    # An invoice for an unknown cell is refused before anything is committed.
    bad = invoice.model_copy(
        update={"expense_id": "bad", "allocations": (CostAllocation(cell_id="nope", weight="1"),)}
    )
    with pytest.raises(ValueError, match="unknown cell"):
        await store.attach_expense(spec.experiment_id, "bad", AT, bad)
    await store.attach_expense(spec.experiment_id, "ok", AT, invoice)
    await store.advance(spec.experiment_id, "first", AT)
    await store.advance(spec.experiment_id, "second", AT)
    await store.complete_expenses(spec.experiment_id, "done", AT, "all bills checked")
    with pytest.raises(ValueError, match="already declared complete"):
        await store.attach_expense(
            spec.experiment_id, "late", AT, invoice.model_copy(update={"expense_id": "late"})
        )
    with pytest.raises(ValueError, match="already declared complete"):
        await store.complete_expenses(spec.experiment_id, "again", AT, "twice")
