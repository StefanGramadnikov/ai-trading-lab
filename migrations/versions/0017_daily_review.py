"""Daily reviews and the suggestions they raise (advice only; nothing is applied by code)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_daily_review"
down_revision: str | None = "0016_alert_state"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "daily_reviews",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("delivered_at_utc", sa.DateTime(timezone=True)),
        sa.Column("delivery_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "review_suggestions",
        sa.Column("suggestion_id", sa.String(200), primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("strategy_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("text", sa.String(600), nullable=False),
        sa.Column("suggestion", sa.String(800), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at_utc", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("decision_reason", sa.String(300)),
    )


def downgrade() -> None:
    op.drop_table("review_suggestions")
    op.drop_table("daily_reviews")
