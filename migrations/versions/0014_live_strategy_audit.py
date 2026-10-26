"""Audit trail for strategy on/off changes."""

import sqlalchemy as sa
from alembic import op

revision: str = "0014_live_strategy_audit"
down_revision: str | None = "0013_live_decision_detail"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "live_strategy_audit",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
    )
    op.create_index(
        "ix_live_strategy_audit_strategy", "live_strategy_audit", ["strategy_id", "seq"]
    )


def downgrade() -> None:
    op.drop_table("live_strategy_audit")
