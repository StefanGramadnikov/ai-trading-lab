"""Bind AI run configuration/runtime durably before any provider request."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006_ai_run_starts"
down_revision: str | None = "0005_ai_runtime"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ai_run_starts",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("input", pg.JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("engine_hash", sa.String(64), nullable=False),
    )
    # Completed pre-checkpoint development runs retain their original identity.
    # Incomplete old calls have no full configuration and are refused by runtime.
    op.execute(
        sa.text("""
        INSERT INTO ai_run_starts (run_id, input, input_hash, engine_hash)
        SELECT run_id, input, input_hash, report->>'engine_hash' FROM ai_runs
        WHERE report->>'engine_hash' ~ '^[0-9a-f]{64}$'
    """)
    )


def downgrade() -> None:
    op.drop_table("ai_run_starts")
