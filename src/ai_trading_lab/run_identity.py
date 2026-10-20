"""Shared run ownership prevents cross-runner accounting and checkpoint collisions."""

from uuid import UUID as UUIDValue

from sqlalchemy import Column, String, Table, select, text
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ai_trading_lab.market_data.storage import metadata

run_owners = Table(
    "run_owners",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("owner_kind", String(32), nullable=False),
    Column("owner_id", UUID(as_uuid=True), nullable=False),
    Column("config_hash", String(64), nullable=False),
)


async def claim_run(
    connection: AsyncConnection,
    run_id: UUIDValue,
    owner_kind: str,
    owner_id: UUIDValue,
    config_hash: str,
) -> None:
    # Sort multi-run claims at the caller; all entry points use this same lock.
    key = int(run_id.hex[:16], 16) - (1 << 63)
    await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    saved = (
        (await connection.execute(select(run_owners).where(run_owners.c.run_id == run_id)))
        .mappings()
        .one_or_none()
    )
    expected = dict(
        run_id=run_id, owner_kind=owner_kind, owner_id=owner_id, config_hash=config_hash
    )
    if saved is not None:
        if dict(saved) != expected:
            raise ValueError(
                "run identity is immutable; already owned by another configuration or runner"
            )
        return
    # Old completed and interrupted runs predate the registry. Preserve their
    # namespace; lack of a new owner row is never permission to appropriate them.
    if owner_kind != "research" and await connection.scalar(
        text("SELECT EXISTS(SELECT 1 FROM research_runs WHERE run_id=:run_id)"), {"run_id": run_id}
    ):
        raise ValueError("legacy research run identity already owned")
    if owner_kind != "ai" and await connection.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM ai_run_starts WHERE run_id=:run_id) "
            "OR EXISTS(SELECT 1 FROM ai_calls WHERE reservation->>'run_id'=:run_text) "
            "OR EXISTS(SELECT 1 FROM ai_runs WHERE run_id=:run_id)"
        ),
        {"run_id": run_id, "run_text": str(run_id)},
    ):
        raise ValueError("legacy AI run identity already owned")
    if await connection.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM experiments e, "
            "LATERAL jsonb_array_elements(e.config->'cells') c "
            "WHERE COALESCE(c->'rules'->>'run_id', c->'ai'->'research'->>'run_id')=:run_text)"
        ),
        {"run_text": str(run_id)},
    ):
        raise ValueError("legacy experiment run identity already owned")
    await connection.execute(insert(run_owners).values(**expected))
