"""The evaluation report on PostgreSQL, from a real multi-cycle run of the three strategies."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.config import Settings
from ai_trading_lab.live.store import (
    live_decisions,
    live_equity,
    live_strategies,
    live_strategy_audit,
)
from ai_trading_lab.report import cli
from ai_trading_lab.report.render import markdown
from ai_trading_lab.report.service import ReportService
from tests.integration.test_live_ai_engine import AIRig, Stub, start
from tests.integration.test_live_ai_engine import engine as engine  # noqa: F401 (fixture)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
D = Decimal
SETTINGS = Settings(_env_file=None, ai_enabled=True, ai_total_limit_usd=D("10"))
WINDOW = timedelta(minutes=285)  # first cycle, three more 15 minutes apart, one 4 hours later


async def _wipe(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in (live_decisions, live_equity, live_strategy_audit):
            await connection.execute(delete(table))
        await connection.execute(delete(live_strategies))


@pytest_asyncio.fixture
async def rig(engine: AsyncEngine) -> AsyncIterator[AIRig]:  # noqa: F811
    await _wipe(engine)
    stub = Stub(lambda body: "buy" if body["pair"] != "SOLUSD" else "abstain")
    value = await start(engine, stub, provider="openai", settings=SETTINGS)
    await value.cycle()
    for _ in range(3):
        await value.cycle(minutes_later=15)
    await value.cycle(minutes_later=240)  # the AI is due again: its second set of calls
    try:
        yield value
    finally:
        await _wipe(engine)


def service(rig: AIRig) -> ReportService:
    return ReportService(rig.engine, SETTINGS, clock=lambda: rig.now)


async def test_a_short_real_run_gives_every_strategy_a_result_and_extends_the_run(
    rig: AIRig,
) -> None:
    report = await service(rig).build()
    assert [s.kind for s in report.strategies] == ["rules", "ai", "benchmark"]
    assert report.window_start is not None and report.window_end is not None
    assert report.window_end - report.window_start == WINDOW
    by_kind = {s.kind: s for s in report.strategies}
    assert all(s.start_equity is not None and s.pnl is not None for s in by_kind.values())
    assert by_kind["ai"].max_drawdown >= 0 and by_kind["benchmark"].fees >= 0
    # Equity is recorded after a cycle's fills: buys in the window's first cycle are already in the
    # starting equity and are not counted again as entries or fees.
    bought = [
        d
        for d in await rig.store.decisions(rig.live.benchmark_id)
        if d.action == "buy" and d.status in ("filled", "partial")
    ]
    assert len(bought) > by_kind["benchmark"].entries
    assert by_kind["benchmark"].entries == sum(
        1 for d in bought if d.cycle_at_utc > report.window_start
    )
    assert report.ai_cost.calls > 0 and report.ai_cost.spent_usd > 0  # the bill is really read
    assert report.ai_cost.cost_eur == report.ai_cost.spent_usd + report.ai_cost.held_usd
    ai_pnl = by_kind["ai"].pnl
    assert ai_pnl is not None and report.ai_net_pnl == ai_pnl - report.ai_cost.cost_eur
    assert {(p.label, p.versus) for p in report.paired} == {
        ("AI", "Buy and hold"),
        ("AI", "Rules breakout"),
        ("Rules breakout", "Buy and hold"),
    }
    assert report.integrity.switches_in_window == 0
    # three-quarters of an hour is nowhere near a week: the only honest answer is to keep going
    assert report.verdict.state == "EXTEND" and "window_under_7_days" in report.verdict.missing


async def test_the_window_starts_when_the_youngest_strategy_started(rig: AIRig) -> None:
    # The older strategies have an extra, earlier point: the paired window must ignore it.
    await rig.store.record_equity(
        rig.live.rules_id, rig.now - timedelta(days=2), D(100), D(100), D(0)
    )
    await rig.store.record_equity(
        rig.live.benchmark_id, rig.now - timedelta(days=2), D(100), D(0), D(100)
    )
    report = await service(rig).build()
    assert report.window_start is not None
    assert report.window_end - report.window_start == WINDOW  # type: ignore[operator]


async def test_since_and_until_narrow_the_window_and_the_rate_changes_the_ai_bill(
    rig: AIRig,
) -> None:
    full = await service(rig).build()
    assert full.window_start is not None and full.window_end is not None
    assert full.ai_cost.calls > 0 and full.ai_cost.spent_usd > 0  # the second set, not the first
    early = await service(rig).build(until=full.window_start + timedelta(minutes=30))
    assert early.window_end - early.window_start == timedelta(minutes=30)  # type: ignore[operator]
    assert early.ai_cost.calls == 0  # the first cycle's calls belong to the starting equity
    late = await service(rig).build(
        since=full.window_start + timedelta(minutes=15), usd_to_eur=D("0.5")
    )
    assert late.window_end - late.window_start == WINDOW - timedelta(minutes=15)  # type: ignore[operator]
    assert late.ai_cost.usd_to_eur == D("0.5") and late.ai_cost.calls == full.ai_cost.calls
    assert late.ai_cost.cost_eur == (late.ai_cost.spent_usd + late.ai_cost.held_usd) / 2


async def test_with_nothing_recorded_the_report_says_so_and_still_renders(
    engine: AsyncEngine,  # noqa: F811
) -> None:
    await _wipe(engine)
    report = await ReportService(engine, SETTINGS).build()
    assert report.window_start is None and report.strategies == []
    assert report.verdict.state == "EXTEND" and report.verdict.missing == ["no_ai_data"]
    assert "no data yet" in markdown(report)


async def test_the_markdown_is_plain_and_carries_the_verdict_the_assumptions_and_thresholds(
    rig: AIRig,
) -> None:
    text = markdown(await service(rig).build())
    assert text.startswith("# AI Trading Lab: evaluation report (paper only)")
    for needle in (
        "## Verdict: EXTEND",
        "less than 7 days of paired time",
        "| Rules breakout |",
        "| Buy and hold |",
        "## AI cost",
        "usage estimates, not an invoice",
        "## Thresholds used (proposed, awaiting the owner's approval)",
        "minimum window days: 7",
        "never approval",
    ):
        assert needle in text
    assert "sk-" not in text


async def test_the_command_line_writes_markdown_or_json_and_rejects_bad_input(
    rig: AIRig,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ATL_DATABASE_URL", database_url)
    code = await cli.run(cli.parser().parse_args([]))
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("# AI Trading Lab: evaluation report (paper only)")
    target = tmp_path / "report.json"
    code = await cli.run(cli.parser().parse_args(["--format", "json", "--out", str(target)]))
    assert code == 0 and json.loads(capsys.readouterr().out)["status"] == "written"
    data = json.loads(target.read_text())
    assert data["verdict"]["state"] == "EXTEND" and len(data["strategies"]) == 3
    for bad in (
        ["--usd-eur", "nan"],
        ["--usd-eur", "inf"],
        ["--usd-eur", "0"],
        ["--usd-eur", "abc"],
        ["--since", "yesterday"],
        ["--since", "2026-10-29T12:00:00"],  # a time without a zone is ambiguous
        ["--format", "pdf"],
    ):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(bad)


async def test_missing_cycles_of_the_worst_strategy_lower_the_coverage(rig: AIRig) -> None:
    start = (await service(rig).build()).window_start
    assert start is not None
    first_hour = start + timedelta(minutes=45)  # four cycles, 15 minutes apart
    full = await service(rig).build(until=first_hour)
    assert full.integrity.coverage == 1
    async with rig.engine.begin() as connection:
        await connection.execute(
            delete(live_equity).where(
                live_equity.c.strategy_id == rig.live.ai_id,
                live_equity.c.at_utc > start,
                live_equity.c.at_utc < first_hour,
            )
        )
    gappy = await service(rig).build(until=first_hour)
    assert gappy.integrity.coverage < D("0.9")  # the AI missed cycles though the others did not
    assert "loop_coverage_under_90_percent" in gappy.verdict.missing


async def test_old_data_is_flagged_unless_the_window_was_chosen(rig: AIRig) -> None:
    later = ReportService(rig.engine, SETTINGS, clock=lambda: rig.now + timedelta(days=3))
    stale = await later.build()
    assert stale.integrity.stale and "data_not_current" in stale.verdict.missing
    assert stale.window_end is not None
    chosen = await later.build(until=stale.window_end)
    assert not chosen.integrity.stale  # an explicit --until is a deliberate look back


async def test_a_switch_inside_the_window_is_counted_and_extends_the_run(rig: AIRig) -> None:
    full = await service(rig).build()
    assert full.window_start is not None
    await rig.store.set_enabled(
        rig.live.rules_id,
        False,
        actor="test",
        reason="paused",
        at=full.window_start + timedelta(minutes=20),
    )
    report = await service(rig).build()
    assert report.integrity.switches_in_window == 1
    assert "strategy_switched_in_window" in report.verdict.missing


async def test_an_abandoned_row_of_the_same_kind_does_not_clamp_the_window(rig: AIRig) -> None:
    await rig.store.register("old-ai", "ai", uuid4(), {}, rig.now - timedelta(days=5))
    for days in (5, 4):
        await rig.store.record_equity(
            "old-ai", rig.now - timedelta(days=days), D(100), D(100), D(0)
        )
    report = await service(rig).build()
    assert report.integrity.ignored_strategies == ["old-ai"]
    assert {s.kind: s.strategy_id for s in report.strategies}["ai"] == rig.live.ai_id
    assert report.window_start is not None and report.window_end is not None
    assert report.window_end - report.window_start == WINDOW
