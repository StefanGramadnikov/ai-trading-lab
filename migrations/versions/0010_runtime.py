"""Immutable local paper runtime configuration and chained event checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010_runtime"
down_revision: str | None = "0009_notifications"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_sessions",
        sa.Column("session_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("engine_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("checkpoint", pg.JSONB, nullable=False),
        sa.Column("checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("chain_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "runtime_events",
        sa.Column(
            "session_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("runtime_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer, primary_key=True),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("event", pg.JSONB, nullable=False),
        sa.Column("checkpoint", pg.JSONB, nullable=False),
        sa.Column("checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("chain_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("session_id", "event_key", name="uq_runtime_event_key"),
    )


def downgrade() -> None:
    op.drop_table("runtime_events")
    op.drop_table("runtime_sessions")
