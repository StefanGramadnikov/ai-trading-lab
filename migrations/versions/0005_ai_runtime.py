"""Immutable AI run records and durable pre-request spending reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005_ai_runtime"
down_revision: str | None = "0004_strategy_research"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ai_budgets",
        sa.Column("budget_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy", pg.JSONB(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("calls_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "ai_calls",
        sa.Column("call_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "budget_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("ai_budgets.budget_id"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reservation", pg.JSONB(), nullable=False),
        sa.Column("reservation_hash", sa.String(64), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
    )
    op.create_table(
        "ai_runs",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input", pg.JSONB(), nullable=False),
        sa.Column("report", pg.JSONB(), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    for name in ("ai_runs", "ai_calls", "ai_budgets"):
        op.drop_table(name)
