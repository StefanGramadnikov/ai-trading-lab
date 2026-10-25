"""Decision detail: the reasoning behind a decision, such as the AI call and its answer."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013_live_decision_detail"
down_revision: str | None = "0012_live_loop"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("live_decisions", sa.Column("detail", pg.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("live_decisions", "detail")
