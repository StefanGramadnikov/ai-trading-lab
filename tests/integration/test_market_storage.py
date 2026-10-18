from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_trading_lab.market_data.collector import replay
from ai_trading_lab.market_data.models import (
    Batch,
    Capture,
    DataMode,
    DataQuality,
    Failure,
    Instrument,
    Observation,
    Source,
)
from ai_trading_lab.market_data.replay import CaptureArchive
from ai_trading_lab.market_data.storage import (
    ObservationStore,
    markets,
    raw_events,
    runs,
    snapshots,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def sample() -> Batch:
    capture = Capture(
        source=Source.DEXSCREENER,
        method="GET",
        path="/token-profiles/latest/v1",
        body='{"price":0.1234567890123456789012345678901}',
        received_at_utc=NOW,
        data_mode=DataMode.FIXTURE,
    )
    observation = Observation(
        capture_id=capture.capture_id,
        instrument=Instrument(
            source=Source.DEXSCREENER, venue="synthetic", native_id=str(uuid4()), kind="spot"
        ),
        received_at_utc=NOW,
        price=Decimal("0.1234567890123456789012345678901"),
        price_currency="USD",
    )
    return Batch(captures=(capture,), observations=(observation,))


@pytest.mark.asyncio
async def test_decimal_provenance_roundtrip_and_idempotent_redelivery(engine: AsyncEngine) -> None:
    batch = sample()
    store = ObservationStore(engine)
    summary = await store.save(Source.DEXSCREENER, batch)
    assert summary.status == "partial"
    assert summary.captures_added == summary.observations_added == 1
    again = await store.save(Source.DEXSCREENER, batch)
    assert again.captures_added == again.observations_added == 0
    restored = await store.read(batch.captures[0].capture_id)
    assert restored[0] == batch.observations[0].assessed()
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(snapshots.c.price, raw_events.c.data_mode, raw_events.c.body)
                .join(raw_events)
                .where(snapshots.c.capture_id == batch.captures[0].capture_id)
            )
        ).one()
        assert row.price == batch.observations[0].price
        assert row.data_mode == "FIXTURE"
        assert row.body == batch.captures[0].body


@pytest.mark.asyncio
async def test_identical_price_polls_and_changed_metadata_are_separate_evidence(
    engine: AsyncEngine,
) -> None:
    batch = sample()
    store = ObservationStore(engine)
    await store.save(Source.DEXSCREENER, batch)
    capture = batch.captures[0].model_copy(update={"capture_id": uuid4()})
    original = batch.observations[0]
    second = original.model_copy(update={"capture_id": capture.capture_id})
    await store.save(Source.DEXSCREENER, Batch(captures=(capture,), observations=(second,)))
    new_capture = capture.model_copy(update={"capture_id": uuid4()})
    changed = second.model_copy(
        update={
            "capture_id": new_capture.capture_id,
            "instrument": original.instrument.model_copy(update={"title": "New metadata"}),
        }
    )
    await store.save(Source.DEXSCREENER, Batch(captures=(new_capture,), observations=(changed,)))
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(markets)
                .where(markets.c.native_id == original.instrument.native_id)
            )
            == 2
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(snapshots)
                .where(
                    snapshots.c.capture_id.in_(
                        [batch.captures[0].capture_id, capture.capture_id, new_capture.capture_id]
                    )
                )
            )
            == 3
        )


@pytest.mark.asyncio
async def test_collision_rolls_back_whole_batch(engine: AsyncEngine) -> None:
    batch = sample()
    store = ObservationStore(engine)
    await store.save(Source.DEXSCREENER, batch)
    extra = batch.captures[0].model_copy(update={"capture_id": uuid4()})
    changed = batch.observations[0].model_copy(update={"price": Decimal("0.5")})
    async with engine.connect() as connection:
        before = await connection.scalar(select(func.count()).select_from(runs))
    with pytest.raises(ValueError, match="Snapshot ID collision"):
        await store.save(
            Source.DEXSCREENER, Batch(captures=(*batch.captures, extra), observations=(changed,))
        )
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(raw_events)
                .where(raw_events.c.capture_id == extra.capture_id)
            )
            == 0
        )
        assert await connection.scalar(select(func.count()).select_from(runs)) == before
    with pytest.raises(ValueError, match="Capture ID collision"):
        await store.save(
            Source.DEXSCREENER,
            Batch(captures=(batch.captures[0].model_copy(update={"body": "{}"}),)),
        )


@pytest.mark.asyncio
async def test_mismatched_observation_and_capture_duplicates_rejected(engine: AsyncEngine) -> None:
    batch = sample()
    store = ObservationStore(engine)
    for bad in [
        Batch(observations=batch.observations),
        Batch(
            captures=(batch.captures[0].model_copy(update={"status_code": 503}),),
            observations=batch.observations,
        ),
        Batch(captures=(batch.captures[0].model_copy(update={"source": Source.SOLANA}),)),
    ]:
        with pytest.raises(ValueError):
            await store.save(Source.DEXSCREENER, bad)
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        await store.save(
            Source.DEXSCREENER,
            Batch(captures=(*batch.captures, batch.captures[0].model_copy(update={"body": "[]"}))),
        )


@pytest.mark.asyncio
async def test_failed_empty_and_complete_run_outcomes(engine: AsyncEngine) -> None:
    store = ObservationStore(engine)
    assert (await store.save(Source.SOLANA, Batch())).status == "empty"
    failure = Failure(source=Source.SOLANA, target="mint", code="timeout", message="unavailable")
    assert (await store.save(Source.SOLANA, Batch(failures=(failure,)))).status == "failed"
    with pytest.raises(ValueError, match="Failure source"):
        await store.save(Source.KALSHI, Batch(failures=(failure,)))
    batch = sample()
    observation = batch.observations[0].model_copy(
        update={"quality": DataQuality.VALID, "event_time_utc": NOW}
    )
    assert (
        await store.save(
            Source.DEXSCREENER, Batch(captures=batch.captures, observations=(observation,))
        )
    ).status == "complete"


@pytest.mark.asyncio
async def test_fixture_to_offline_replay_to_postgres(engine: AsyncEngine) -> None:
    # A synthetic empty discovery response proves the CLI path need not contact external services.
    capture = Capture(
        source=Source.DEXSCREENER,
        method="GET",
        path="/token-profiles/latest/v1",
        body="[]",
        received_at_utc=NOW,
        data_mode=DataMode.FIXTURE,
    )
    batch = await replay(CaptureArchive(source=Source.DEXSCREENER, captures=(capture,)))
    summary = await ObservationStore(engine).save(Source.DEXSCREENER, batch)
    assert summary.status == "empty"
    assert summary.captures_added == 1
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(raw_events.c.data_mode).where(
                    raw_events.c.capture_id == batch.captures[0].capture_id
                )
            )
            == "REPLAY"
        )


@pytest.mark.asyncio
async def test_malformed_compression_cli_persists_failed_run(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    from contextlib import asynccontextmanager

    import httpx2

    from ai_trading_lab.market_data import cli
    from ai_trading_lab.market_data.transport import HttpTransport

    def response(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-encoding": "gzip"}, content=b"invalid gzip", request=request
        )

    @asynccontextmanager
    async def transport() -> AsyncIterator[HttpTransport]:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as client:
            async with HttpTransport(client=client) as bounded:
                yield bounded

    monkeypatch.setattr(cli, "HttpTransport", transport)
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    arguments = cli.parser().parse_args(
        ["collect", "--source", "solana", "--mint", "So11111111111111111111111111111111111111112"]
    )
    assert await cli.run(arguments) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["failure_codes"] == ["invalid_content_encoding", "invalid_content_encoding"]
    assert output["status"] == "failed"
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(runs.c.failures, runs.c.capture_count, runs.c.observation_count).where(
                    runs.c.run_id == output["run_id"]
                )
            )
        ).one()
        assert [failure["code"] for failure in row.failures] == output["failure_codes"]
        assert row.capture_count == row.observation_count == 0
