"""Measured synthetic OBSERVED-shaped real-reducer journal scaling acceptance.

This is a deterministic fixture workload, not an operational source/throughput claim.
It retains 40 KB of original synthetic raw metadata per cycle and exercises fills.
"""

import hashlib
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.market_data.models import DataMode
from ai_trading_lab.paper_execution.replay import ReplayEntry
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.evidence import observed_quote
from ai_trading_lab.runtime.journal import Journal
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.strategies.models import StrategyFrame
from tests.backtest_helpers import research
from tests.unit.test_paper_replay import entry_data
from tests.unit.test_runtime_engine import config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PADDING = "".join(
    hashlib.sha256(f"synthetic-benchmark-{index}".encode()).hexdigest() for index in range(625)
)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    value = create_async_engine(database_url)
    try:
        yield value
    finally:
        await value.dispose()


def observed_payload(index: int, start: datetime) -> tuple[datetime, dict[str, Any]]:
    at = start + timedelta(seconds=index)
    raw: dict[str, Any] = entry_data()
    metadata = raw["metadata_capture"]
    metadata["capture_id"] = str(uuid4())
    metadata["received_at_utc"] = at.isoformat()
    body = json.loads(metadata["body"])
    body[0]["synthetic_benchmark_padding"] = PADDING
    metadata["body"] = json.dumps(body)
    book = raw["book_capture"]
    book["capture_id"] = str(uuid4())
    book["received_at_utc"] = at.isoformat()
    body = json.loads(book["body"])
    body["timestamp"] = str(int(at.timestamp() * 1000))
    book["body"] = json.dumps(body)
    entry = ReplayEntry.model_validate(raw)
    quote = observed_quote(entry)
    forecast = None
    if index <= 6:
        original = research().frames[0].forecast
        assert original is not None
        forecast = original.model_copy(
            update={
                "instrument_key": quote.instrument.key,
                "market_id": quote.instrument.market_id,
                "outcome": quote.instrument.outcome,
                "resolution_terms_hash": entry.resolution_terms_hash,
                "data_mode": DataMode.OBSERVED,
                "produced_at_utc": at,
                "available_at_utc": at,
                "probability": Decimal("0.9") if index % 2 else Decimal("0.1"),
            }
        )
    frame = StrategyFrame(
        quote=quote, at_utc=at, forecast=forecast, resolution_terms_hash=entry.resolution_terms_hash
    )
    return at, {
        "action": "tick",
        "frame": frame.model_dump(mode="json"),
        "source_entry": entry.model_dump(mode="json"),
    }


async def relation_bytes(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        size = await connection.scalar(
            text(
                "SELECT pg_total_relation_size('runtime_events') "
                "+ pg_total_relation_size('runtime_sessions')"
            )
        )
    assert isinstance(size, int)
    return size


async def test_real_reducer_500_2000_6000_large_original_frames(engine: AsyncEngine) -> None:
    base = ReplayEntry.model_validate(entry_data())
    raw = config()
    raw.update(
        data_mode="OBSERVED",
        instrument=base.instrument.model_dump(mode="json"),
        cash_unit=base.instrument.cash_unit,
        max_cycles=6000,
        max_journal_bytes=512 * 1024 * 1024,
        max_event_bytes=50000,
    )
    raw["profile"]["daily_loss_fraction"] = "1"
    raw["profile"]["drawdown_fraction"] = "1"
    spec = RuntimeConfig.model_validate(raw)
    before = await relation_bytes(engine)
    journal = Journal(engine, reducer)
    await journal.create(spec.session_id, spec.model_dump(mode="json"), spec.started_at_utc)
    maximum = 0.0
    fills = 0
    started = time.perf_counter()
    async with journal.worker(spec.session_id) as active:
        await active.get(spec.session_id)
        for index in range(1, 6001):
            at, payload = observed_payload(index, spec.started_at_utc)
            envelope_bytes = len(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            assert 30000 < envelope_bytes < 50000
            began = time.perf_counter()
            await active.append(spec.session_id, f"poll:{index}", {"action": "poll"}, at)
            maximum = max(maximum, time.perf_counter() - began)
            began = time.perf_counter()
            checkpoint: Any = await active.append(
                spec.session_id, f"poll:{index}:result", payload, at
            )
            maximum = max(maximum, time.perf_counter() - began)
            decision = checkpoint["state"]["last_decision"]
            receipt = decision.get("receipt") if decision else None
            if (
                receipt
                and receipt["result"]["status"] in {"FILLED", "PARTIALLY_FILLED"}
                and index <= 6
            ):
                fills += 1
            assert "history" not in checkpoint["state"]
            if index in {500, 2000, 6000}:
                began = time.perf_counter()
                recovered = await Journal(engine, reducer).get(spec.session_id)
                recovery = time.perf_counter() - began
                assert recovered == checkpoint
                # Accepted budgets (CODEX_FIXES F-03). Appends were ~0.04 s when measured; the
                # 2 s limit leaves wide CI margin yet fails an O(history) regression. Cold
                # recovery runs under the journal's 10 s transaction bound, so it must stay
                # below it even at the largest declared session.
                assert maximum < 2.0, f"append took {maximum:.3f}s at {index} cycles"
                assert recovery < 9.0, f"cold recovery took {recovery:.3f}s at {index} cycles"
                assert checkpoint["sequence"] == 2 * index
                assert checkpoint["journal_bytes"] < 50000 * index
                assert fills == 6
                physical = await relation_bytes(engine)
                print(
                    json.dumps(
                        {
                            "cycles": index,
                            "events": checkpoint["sequence"],
                            "canonical_journal_bytes": checkpoint["journal_bytes"],
                            "max_append_seconds": maximum,
                            "recovery_seconds": recovery,
                            "elapsed_seconds": time.perf_counter() - started,
                            "relation_bytes": physical,
                            "relation_growth_bytes": physical - before,
                            "fills": fills,
                            "status": checkpoint["state"]["status"],
                            "envelope_bytes": envelope_bytes,
                        }
                    ),
                    flush=True,
                )
    assert checkpoint["state"]["status"] == "STOPPED"
    assert checkpoint["journal_bytes"] < spec.max_journal_bytes
