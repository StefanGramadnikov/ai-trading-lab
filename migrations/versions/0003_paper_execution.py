"""Fixture-only paper portfolios, journals and durable IOC results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_paper_execution"
down_revision: str | None = "0002_market_data"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "paper_portfolios",
        sa.Column("portfolio_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("state", pg.JSONB(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "paper_orders",
        sa.Column("order_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("receipt", pg.JSONB(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "paper_journal",
        sa.Column("entry_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            nullable=False,
        ),
        sa.Column("command_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
    )
    op.create_table(
        "paper_events",
        sa.Column("event_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
    )
    op.create_table(
        "paper_fills",
        sa.Column(
            "order_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_orders.order_id"),
            primary_key=True,
        ),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("notional", sa.Numeric(), nullable=False),
        sa.Column("fee", sa.Numeric(), nullable=False),
    )
    op.create_table(
        "paper_liquidity",
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            primary_key=True,
        ),
        sa.Column("snapshot_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("quote", pg.JSONB(), nullable=False),
        sa.Column("buy", pg.JSONB(), nullable=False),
        sa.Column("sell", pg.JSONB(), nullable=False),
    )
    op.create_table(
        "paper_instruments",
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            primary_key=True,
        ),
        sa.Column("identity", sa.String(64), primary_key=True),
        sa.Column("definition", pg.JSONB(), nullable=False),
        sa.Column("settled", sa.Integer(), nullable=False),
    )
    op.create_table(
        "paper_commands",
        sa.Column("command_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portfolio_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("paper_portfolios.portfolio_id"),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", pg.JSONB(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "paper_commands",
        "paper_instruments",
        "paper_liquidity",
        "paper_fills",
        "paper_events",
        "paper_journal",
        "paper_orders",
        "paper_portfolios",
    ):
        op.drop_table(table)
