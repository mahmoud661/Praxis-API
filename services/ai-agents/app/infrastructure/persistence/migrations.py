from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from .postgres_connection import PostgresConnection


def _alembic_cfg() -> Config:
    """Build Alembic's Config from alembic.ini at the project root."""
    # File lives at app/infrastructure/persistence/migrations.py
    # parents[3] = ai-agents/ (the project root that contains alembic.ini)
    repo_root = Path(__file__).resolve().parents[3]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    return cfg


async def run_migrations(conn: PostgresConnection) -> None:
    """Apply `alembic upgrade head` at boot. Synchronous under the hood
    (psycopg2), runs before the app accepts traffic."""
    os.environ.setdefault("DATABASE_URL", conn.dsn)
    command.upgrade(_alembic_cfg(), "head")
