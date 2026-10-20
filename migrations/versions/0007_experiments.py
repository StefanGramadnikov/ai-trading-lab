"""Immutable experiment checkpoints and append-only lifecycle commands."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007_experiments"
down_revision: str | None = "0006_ai_run_starts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("experiment_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("engine_hash", sa.String(64), nullable=False),
        sa.Column("checkpoint", pg.JSONB(), nullable=False),
        sa.Column("checkpoint_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "experiment_events",
        sa.Column(
            "experiment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("experiments.experiment_id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("experiment_id", "key", name="uq_experiment_event_key"),
    )


def downgrade() -> None:
    op.drop_table("experiment_events")
    op.drop_table("experiments")
