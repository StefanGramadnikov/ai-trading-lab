"""Invalidate verified runtime continuations after any historical evidence mutation."""

import sqlalchemy as sa
from alembic import op

revision: str = "0011_runtime_generation"
down_revision: str | None = "0010_runtime"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_sessions",
        sa.Column("mutation_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("""
        CREATE FUNCTION runtime_evidence_mutated() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                UPDATE runtime_sessions SET mutation_generation = mutation_generation + 1;
                RETURN NULL;
            END IF;
            IF TG_OP <> 'INSERT' THEN
                UPDATE runtime_sessions SET mutation_generation = mutation_generation + 1
                  WHERE session_id = OLD.session_id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                UPDATE runtime_sessions SET mutation_generation = mutation_generation + 1
                  WHERE session_id = NEW.session_id;
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER runtime_evidence_mutation AFTER INSERT OR UPDATE OR DELETE "
        "ON runtime_events FOR EACH ROW EXECUTE FUNCTION runtime_evidence_mutated()"
    )
    op.execute(
        "CREATE TRIGGER runtime_evidence_truncate AFTER TRUNCATE ON runtime_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION runtime_evidence_mutated()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER runtime_evidence_truncate ON runtime_events")
    op.execute("DROP TRIGGER runtime_evidence_mutation ON runtime_events")
    op.execute("DROP FUNCTION runtime_evidence_mutated()")
    op.drop_column("runtime_sessions", "mutation_generation")
