"""Offline provenance boundary regressions; synthetic inputs are never run evidence."""

import argparse
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx2
import pytest
from pydantic import ValidationError

from ai_trading_lab.config import Settings
from ai_trading_lab.market_data.models import Capture, DataMode, Source
from ai_trading_lab.market_data.transport import HttpTransport
from ai_trading_lab.runtime import cli
from ai_trading_lab.runtime.engine import reducer
from ai_trading_lab.runtime.evidence import observed_quote
from ai_trading_lab.runtime.journal import Journal
from tests.backtest_helpers import research
from tests.unit.test_paper_replay import NOW
from tests.unit.test_runtime_engine import tick
from tests.unit.test_runtime_source import observed_config


def test_capture_requires_explicit_provenance() -> None:
    with pytest.raises(ValidationError, match="data_mode"):
        Capture.model_validate(
            dict(source="polymarket", method="GET", path="/markets", body="[]", received_at_utc=NOW)
        )


@pytest.mark.asyncio
async def test_injected_http_client_cannot_claim_observed() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=[]))
    ) as client:
        with pytest.raises(ValueError, match="OBSERVED"):
            HttpTransport(client, data_mode=DataMode.OBSERVED)
        transport = HttpTransport(client)
        capture = await transport.request(Source.POLYMARKET, "GET", "/markets")
        assert capture.data_mode == DataMode.FIXTURE


@pytest.mark.parametrize(
    "pending,key", [(False, "poll:1:result"), (True, "forged"), (True, "poll:2:result")]
)
def test_observed_tick_requires_matching_pending_poll(pending: bool, key: str) -> None:
    spec, entry = observed_config()
    at = entry.book_capture.received_at_utc.isoformat()
    events: tuple[dict[str, object], ...] = ()
    if pending:
        events = ({"key": "poll:1", "at": at, "payload": {"action": "poll"}},)
    quote = observed_quote(entry)
    frame = (
        research()
        .frames[0]
        .model_copy(
            update={
                "quote": quote,
                "at_utc": quote.received_at_utc,
                "forecast": None,
                "resolution_terms_hash": entry.resolution_terms_hash,
            }
        )
    )
    event = {**tick(frame, 1, entry), "key": key}
    with pytest.raises(ValueError, match="runtime_observed_poll_result_required"):
        reducer(spec.model_dump(mode="json"), (*events, event))


@pytest.mark.asyncio
async def test_cli_refuses_observed_step_before_reading_input() -> None:
    spec, _ = observed_config()
    engine, journal = AsyncMock(), AsyncMock()
    journal.config.return_value = spec.model_dump(mode="json")
    args = argparse.Namespace(
        command="step", session_id=spec.session_id, event="unused", key="poll:1:result", at=NOW
    )
    with (
        patch.object(cli, "Settings", return_value=Settings(_env_file=None)),
        patch.object(cli, "create_database_engine", return_value=engine),
        patch.object(cli, "Journal", return_value=journal),
        patch.object(cli, "read_json") as read,
    ):
        with pytest.raises(ValueError, match="OBSERVED"):
            await cli.run(args)
    read.assert_not_called()
    journal.append.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["poll", "tick", "failure"])
async def test_observed_journal_writes_require_worker_lease(action: str) -> None:
    spec, _ = observed_config()
    journal = Journal(AsyncMock(), reducer)

    @asynccontextmanager
    async def transaction(_: UUID) -> Any:
        yield AsyncMock()

    with (
        patch.object(journal, "_transaction", transaction),
        patch.object(journal, "_lock", AsyncMock()),
        patch.object(
            journal,
            "_load",
            AsyncMock(
                return_value=(
                    spec.model_dump(mode="json"),
                    [
                        {
                            "event_key": "poll:1:result",
                            "event": {
                                "key": "poll:1:result",
                                "at": NOW.isoformat(),
                                "payload": {"action": action},
                            },
                            "checkpoint": {},
                        }
                    ],
                    {},
                )
            ),
        ),
    ):
        with pytest.raises(ValueError, match="worker lease"):
            await journal.append(spec.session_id, "poll:1:result", {"action": action}, NOW)
