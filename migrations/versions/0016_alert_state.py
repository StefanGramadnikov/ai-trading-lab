"""Alert state: which alerts are active and when the owner was last told."""

import sqlalchemy as sa
from alembic import op

revision: str = "0016_alert_state"
down_revision: str | None = "0015_error_journal"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "alert_state",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("rule", sa.String(8), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.String(500), nullable=False),
        sa.Column("first_fired_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_utc", sa.DateTime(timezone=True)),
        sa.Column("resolved_at_utc", sa.DateTime(timezone=True)),
        sa.Column("episode", sa.Integer, nullable=False),
        sa.Column("failed_sends", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("alert_state")
