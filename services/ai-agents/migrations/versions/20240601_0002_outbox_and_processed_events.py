"""outbox + processed_events for idempotent kafka consumption

Revision ID: 0002_outbox_processed
Revises: 0001_initial
Create Date: 2024-06-01 00:00:00.000000
"""

from alembic import op


revision = "0002_outbox_processed"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dedup table for inbound events — closes the at-least-once gap.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_events (
            event_id        VARCHAR(120) NOT NULL,
            consumer_group  VARCHAR(120) NOT NULL,
            processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (event_id, consumer_group)
        )
        """
    )

    # Mirror of auth's outbox — for events ai-agents wants to PUBLISH.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            aggregate_id  VARCHAR(120) NOT NULL,
            topic         VARCHAR(120) NOT NULL,
            event_name    VARCHAR(120) NOT NULL,
            payload       JSONB NOT NULL,
            headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at  TIMESTAMPTZ NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (created_at) WHERE published_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS outbox_pending_idx")
    op.execute("DROP TABLE IF EXISTS outbox")
    op.execute("DROP TABLE IF EXISTS processed_events")
