"""Append-only market definitions, captures, snapshots and ingestion outcomes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_market_data"
down_revision: str | None = "0001_foundation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "raw_source_events",
        sa.Column("capture_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("data_mode", sa.String(), nullable=False),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "markets",
        sa.Column("definition_hash", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("venue", sa.String(), nullable=False),
        sa.Column("native_id", sa.String(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_markets_identity", "markets", ["source", "venue", "native_id"])
    op.create_table(
        "market_snapshots",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "capture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_source_events.capture_id"),
            nullable=False,
        ),
        sa.Column(
            "definition_hash",
            sa.String(64),
            sa.ForeignKey("markets.definition_hash"),
            nullable=False,
        ),
        sa.Column("event_time_utc", sa.DateTime(timezone=True)),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(), nullable=False),
        sa.Column("price", sa.Numeric()),
        sa.Column("liquidity", sa.Numeric()),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("observation", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_snapshots_market_time", "market_snapshots", ["definition_hash", "received_at_utc"]
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("capture_count", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("failures", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_index("ix_snapshots_market_time", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_markets_identity", table_name="markets")
    op.drop_table("markets")
    op.drop_table("raw_source_events")
