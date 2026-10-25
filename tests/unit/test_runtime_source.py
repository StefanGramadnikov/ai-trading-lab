"""Original-shape synthetic captures; no public network in deterministic tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ai_trading_lab.market_data.models import DataMode, Source, TransportError
from ai_trading_lab.paper_execution.replay import ReplayEntry
from ai_trading_lab.runtime.models import RuntimeConfig
from ai_trading_lab.runtime.source import collect_frame
from tests.unit.test_paper_replay import entry_data
from tests.unit.test_runtime_engine import config


def observed_config() -> tuple[RuntimeConfig, ReplayEntry]:
    entry = ReplayEntry.model_validate(entry_data())
    raw: dict[str, Any] = config()
    raw.update(
        data_mode="OBSERVED",
        instrument=entry.instrument.model_dump(mode="json"),
        cash_unit=entry.instrument.cash_unit,
        cash_step=str(entry.instrument.cash_step),
    )
    return RuntimeConfig.model_validate(raw), entry


@pytest.mark.asyncio
async def test_collector_requests_only_pinned_public_instrument_without_forecast() -> None:
    spec, entry = observed_config()
    assert spec.instrument is not None
    metadata = entry.metadata_capture.model_copy(
        update={
            "params": {
                "condition_ids": str(spec.instrument.market_id),
                "limit": "1",
                "active": "true",
                "closed": "false",
            }
        }
    )
    transport = AsyncMock()
    transport.request.side_effect = [metadata, entry.book_capture]
    payload = await collect_frame(spec, transport, clock=lambda: entry.book_capture.received_at_utc)
    assert payload["action"] == "tick"
    frame: Any = payload["frame"]
    assert frame["quote"]["data_mode"] == "OBSERVED"
    assert frame["forecast"] is None and frame["momentum"] is None
    assert transport.request.await_count == 2
    transport.request.assert_any_await(
        Source.POLYMARKET, "GET", "/book", params={"token_id": "123"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["FIXTURE", "REPLAY"])
async def test_original_captures_cannot_be_replaced_with_other_modes(mode: str) -> None:
    spec, entry = observed_config()
    transport = AsyncMock()
    transport.request.side_effect = [
        entry.metadata_capture.model_copy(update={"data_mode": DataMode(mode)}),
        entry.book_capture,
    ]
    with pytest.raises(ValueError):
        await collect_frame(spec, transport, clock=lambda: entry.book_capture.received_at_utc)


@pytest.mark.asyncio
async def test_fixture_session_never_starts_transport() -> None:
    transport = AsyncMock()
    with pytest.raises(ValueError):
        await collect_frame(RuntimeConfig.model_validate(config()), transport)
    transport.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_timeout_is_not_a_synthetic_empty_market() -> None:
    spec, _ = observed_config()
    transport = AsyncMock()
    transport.request.side_effect = TransportError(Source.POLYMARKET, "timeout")
    with pytest.raises(TransportError):
        await collect_frame(spec, transport)
    assert transport.request.await_count == 1
