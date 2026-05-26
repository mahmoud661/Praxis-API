"""initial schema (idempotent baseline)

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw SQL with IF NOT EXISTS so this is safe whether the DB is empty or
    # already has the schema from the previous boot-time-DDL approach.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id            UUID PRIMARY KEY,
            owner_id      UUID NOT NULL,
            name          VARCHAR(120) NOT NULL,
            system_prompt TEXT NOT NULL DEFAULT '',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS agents_owner_idx ON agents(owner_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agents_owner_idx")
    op.execute("DROP TABLE IF EXISTS agents")
