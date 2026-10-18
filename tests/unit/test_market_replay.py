"""Synthetic archives exercise offline request matching, never real market evidence."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_trading_lab.market_data.cli import parser, read_archive, write_archive
from ai_trading_lab.market_data.collector import collect, replay
from ai_trading_lab.market_data.models import Capture, DataMode, Source, TransportError
from ai_trading_lab.market_data.replay import CaptureArchive, ReplayTransport

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def archive() -> CaptureArchive:
    first = Capture(
        source=Source.KALSHI,
        method="GET",
        path="/markets",
        params={"status": "open", "limit": "2"},
        body=json.dumps(
            {
                "markets": [
                    {"ticker": "TEST-MARKET", "market_type": "binary", "title": "Synthetic"}
                ],
                "cursor": "",
            }
        ),
        received_at_utc=NOW,
        data_mode=DataMode.FIXTURE,
    )
    book = Capture(
        source=Source.KALSHI,
        method="GET",
        path="/markets/TEST-MARKET/orderbook",
        params={"depth": "100"},
        body='{"orderbook_fp":{"yes_dollars":[["0.45","2.00"]],"no_dollars":[["0.50","3.00"]]}}',
        received_at_utc=NOW,
        data_mode=DataMode.FIXTURE,
    )
    return CaptureArchive(source=Source.KALSHI, limit=2, captures=(first, book))


@pytest.mark.asyncio
async def test_replay_is_deterministic_preserves_times_and_marks_data_mode() -> None:
    original = archive()
    first = await replay(original)
    second = await replay(original)
    assert first == second
    assert not first.failures
    assert len(first.observations) == 2
    assert all(c.data_mode == DataMode.REPLAY and c.received_at_utc == NOW for c in first.captures)
    assert first.captures[0].origin_capture_id == original.captures[0].capture_id
    assert first.observations[0].event_time_utc is None
    assert first.observations[0].asks[0].derived


@pytest.mark.asyncio
async def test_mismatched_or_exhausted_replay_never_falls_back_to_network() -> None:
    transport = ReplayTransport(archive())
    with pytest.raises(TransportError, match="replay_request_mismatch"):
        await transport.request(Source.KALSHI, "GET", "/markets", params={"limit": "1"})
    for capture in archive().captures:
        await transport.request(capture.source, capture.method, capture.path, params=capture.params)
    with pytest.raises(TransportError, match="replay_exhausted"):
        await transport.request(Source.KALSHI, "GET", "/markets")


def test_archive_rejects_forbidden_paths_duplicates_and_wrong_source() -> None:
    sample = archive()
    with pytest.raises(TransportError, match="request_not_allowed"):
        CaptureArchive(
            source=Source.KALSHI,
            captures=(sample.captures[0].model_copy(update={"path": "/portfolio/orders"}),),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        CaptureArchive(source=Source.KALSHI, captures=(sample.captures[0],) * 2)
    with pytest.raises(ValidationError, match="source mismatch"):
        CaptureArchive(source=Source.SOLANA, captures=sample.captures)


@pytest.mark.asyncio
async def test_replayed_http_failure_and_leftover_responses_are_explicit() -> None:
    sample = archive()
    bad = sample.captures[0].model_copy(update={"status_code": 503})
    batch = await replay(sample.model_copy(update={"captures": (bad, *sample.captures[1:])}))
    assert batch.failures[0].code == "replayed_http_error"
    assert batch.failures[-1].code == "replay_unconsumed"
    assert batch.captures[0].data_mode == DataMode.REPLAY


@pytest.mark.asyncio
@pytest.mark.parametrize("source", list(Source))
async def test_collector_bounds_apply_before_io(source: Source) -> None:
    transport = ReplayTransport(archive())
    with pytest.raises(ValueError):
        await collect(source, transport, limit=0)
    with pytest.raises(ValueError):
        await collect(source, transport, max_pages=4)
    with pytest.raises(ValueError):
        await collect(source, transport, mint_addresses=["x"] * 11)
    assert transport.request_count == 0


def test_archive_files_bounded_and_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    sample = archive()
    write_archive(path, sample)
    assert read_archive(path) == sample
    with pytest.raises(FileExistsError):
        write_archive(path, sample)
    path.write_bytes(b" " * 8_000_001)
    with pytest.raises(ValueError, match="8MB"):
        read_archive(path)


def test_cli_rejects_invalid_source_and_limits() -> None:
    for arguments in [
        ["collect", "--source", "execution"],
        ["collect", "--source", "solana", "--limit", "0"],
        ["collect", "--source", "kalshi", "--max-pages", "4"],
    ]:
        with pytest.raises(SystemExit):
            parser().parse_args(arguments)
