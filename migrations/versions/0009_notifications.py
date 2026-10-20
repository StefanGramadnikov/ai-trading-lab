"""Durable bounded outbound notification attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009_notifications"
down_revision: str | None = "0008_run_ownership"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("event_key", sa.String(200), primary_key=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("event", pg.JSONB, nullable=False),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("claim_id", sa.String(36)),
        sa.Column("destination_hash", sa.String(64)),
        sa.Column("attempted_at_utc", sa.DateTime(timezone=True)),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(40)),
        sa.Column("message_id", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("notification_outbox")
