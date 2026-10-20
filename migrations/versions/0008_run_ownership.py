"""Reserve shared research identities without altering applied experiment tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008_run_ownership"
down_revision: str | None = "0007_experiments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "run_owners",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_kind", sa.String(32), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_owners")
