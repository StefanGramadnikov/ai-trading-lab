"""Bounded error journal: one row per distinct problem plus a few recent occurrences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0015_error_journal"
down_revision: str | None = "0014_live_strategy_audit"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "error_groups",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("component", sa.String(30), nullable=False),
        sa.Column("error_type", sa.String(100), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("headline", sa.String(200), nullable=False),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("first_seen_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_strategy_id", sa.String(100)),
        sa.Column("last_pair", sa.String(12)),
        sa.Column("last_context", pg.JSONB),
        sa.Column("last_message", sa.String(500)),
        sa.Column("last_traceback", sa.Text),
        sa.Column("acknowledged_by", sa.String(100)),
        sa.Column("acknowledged_at_utc", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_reason", sa.String(200)),
    )
    op.create_index("ix_error_groups_last_seen", "error_groups", ["last_seen_utc"])
    op.create_table(
        "error_occurrences",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_id", sa.String(100)),
        sa.Column("pair", sa.String(12)),
        sa.Column("context", pg.JSONB),
        sa.Column("message", sa.String(500)),
    )
    op.create_index("ix_error_occurrences_fp", "error_occurrences", ["fingerprint", "seq"])


def downgrade() -> None:
    op.drop_table("error_occurrences")
    op.drop_table("error_groups")
