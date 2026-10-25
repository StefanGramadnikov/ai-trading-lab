"""V0 live paper loop: strategies, closed candles, decisions, state and equity points."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0012_live_loop"
down_revision: str | None = "0011_runtime_generation"
branch_labels: str | None = None
depends_on: str | None = None

PRICE = sa.Numeric(38, 18)


def upgrade() -> None:
    op.create_table(
        "live_strategies",
        sa.Column("strategy_id", sa.String(100), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("portfolio_id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "live_candles",
        sa.Column("pair", sa.String(12), primary_key=True),
        sa.Column("interval_minutes", sa.Integer, primary_key=True),
        sa.Column("open_time_utc", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("vwap", PRICE, nullable=False),
        sa.Column("volume", PRICE, nullable=False),
        sa.Column("trades", sa.Integer, nullable=False),
    )
    op.create_table(
        "live_decisions",
        sa.Column("decision_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("pair", sa.String(12), nullable=False),
        sa.Column("cycle_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("intent", pg.JSONB),
        sa.Column("evidence", pg.JSONB),
        sa.Column("result", pg.JSONB),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("strategy_id", "idempotency_key", name="uq_live_decision_key"),
    )
    op.create_index(
        "ix_live_decisions_strategy_time", "live_decisions", ["strategy_id", "cycle_at_utc"]
    )
    op.create_table(
        "live_state",
        sa.Column("strategy_id", sa.String(100), primary_key=True),
        sa.Column("state", pg.JSONB, nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "live_equity",
        sa.Column("strategy_id", sa.String(100), primary_key=True),
        sa.Column("at_utc", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("equity", PRICE, nullable=False),
        sa.Column("cash", PRICE, nullable=False),
        sa.Column("positions_value", PRICE, nullable=False),
    )


def downgrade() -> None:
    for table in ("live_equity", "live_state", "live_decisions", "live_candles", "live_strategies"):
        op.drop_table(table)
