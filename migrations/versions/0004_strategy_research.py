"""Immutable rules-only research inputs and decision evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004_strategy_research"
down_revision: str | None = "0003_paper_execution"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column("strategy_hash", sa.String(64), primary_key=True),
        sa.Column("strategy_kind", sa.String(200), nullable=False),
        sa.Column("version", sa.String(200), nullable=False),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.UniqueConstraint("strategy_kind", "version"),
    )
    op.create_table(
        "research_runs",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "strategy_hash",
            sa.String(64),
            sa.ForeignKey("strategy_versions.strategy_hash"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("input", pg.JSONB(), nullable=False),
        sa.Column("report", pg.JSONB(), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "research_decisions",
        sa.Column(
            "run_id", pg.UUID(as_uuid=True), sa.ForeignKey("research_runs.run_id"), primary_key=True
        ),
        sa.Column("index", sa.Integer(), primary_key=True),
        sa.Column("record", pg.JSONB(), nullable=False),
    )


def downgrade() -> None:
    for name in ("research_decisions", "research_runs", "strategy_versions"):
        op.drop_table(name)
