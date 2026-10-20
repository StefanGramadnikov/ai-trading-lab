"""Actual CLI process executes fixtures against isolated PostgreSQL only."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tests.experiment_helpers import experiment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_cli_end_to_end_repeat_and_safe_error(database_url: str, tmp_path: Path) -> None:
    spec = experiment()
    file = tmp_path / "experiment.json"
    file.write_text(spec.model_dump_json())
    env = {
        **os.environ,
        "ATL_DATABASE_URL": database_url,
        "ATL_EXECUTION_MODE": "PAPER_ONLY",
        "ATL_AI_ENABLED": "false",
        "ATL_OPENAI_API_KEY": "",
    }
    eid = str(spec.experiment_id)
    flags = ["--at", "2026-10-20T00:00:00Z"]

    async def command(*args: str, expected: int = 0) -> dict[str, Any]:
        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "ai_trading_lab.experiments.cli", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert process.returncode == expected, process.stdout + process.stderr
        return cast(dict[str, Any], json.loads(process.stdout))

    first = await command("create", str(file), "--key", "create", *flags)
    assert first == await command("create", str(file), "--key", "create", *flags)
    await command("start", eid, "--key", "start", "--reason", "synthetic", *flags)
    a = await command("run", eid, "--key", "cell-1", *flags)
    assert await command("run", eid, "--key", "cell-1", *flags) == a
    await command("run", eid, "--key", "cell-2", *flags)
    report = await command("report", eid)
    assert report == await command("report", eid)
    assert report["lifecycle"]["state"] == "EVALUATING"
    assert all(
        c["equity_curve"]
        and c["equity_curve"][-1]["at_utc"] == report["end_at_utc"].replace("+00:00", "Z")
        for c in report["cells"]
    )
    assert report["evidence_sufficient"] is True and report["empirical_performance_claim"] is False
    approved = await command(
        "approve", eid, "--key", "approved", "--reason", "engineering only", *flags
    )
    assert approved["state"] == "APPROVED"
    failed = await command("run", eid, "--key", "too-late", *flags, expected=2)
    assert failed["status"] == "failed"
