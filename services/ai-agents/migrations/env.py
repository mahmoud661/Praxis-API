"""
Alembic environment.

`DATABASE_URL` is a postgres:// asyncpg DSN used by the app at runtime.
Alembic needs a sync driver, so we coerce postgresql+asyncpg -> postgresql
(falls back to psycopg2) here without forcing the app code to care.
"""

from __future__ import annotations

import os
import re

from alembic import context
from sqlalchemy import engine_from_config, pool


def _sync_dsn(raw: str) -> str:
    # Strip an explicit asyncpg driver if present.
    raw = re.sub(r"\+asyncpg", "", raw)
    # `postgres://` is deprecated in SQLAlchemy 2.x; normalize.
    return re.sub(r"^postgres://", "postgresql://", raw)


config = context.config
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL is required for migrations")
config.set_main_option("sqlalchemy.url", _sync_dsn(db_url))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
