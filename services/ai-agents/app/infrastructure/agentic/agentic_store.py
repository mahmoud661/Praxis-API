"""
AgenticStore — owns the psycopg async connection pool and exposes the two
LangGraph persistence primitives we use:

  - `store`         : `AsyncPostgresStore`  (cross-thread shared data — agent
                      definitions, memories, etc.)
  - `checkpointer`  : `AsyncPostgresSaver`  (per-run graph state — messages,
                      intermediate steps, resumable agent runs)

Both call `.setup()` once at startup to CREATE TABLE IF NOT EXISTS — this is
the entire "migration system" for the ai-agents service. No Alembic, no
SQLAlchemy `create_all`, no hand-rolled DDL.

The pool is shared so both objects use the same set of connections.
"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from ..config.env import Env


class AgenticStore:
    """Lifecycle: constructed eagerly (only needs Env), connected lazily via
    `init()` from the FastAPI lifespan, disposed on shutdown.

    Auto-resolved by the DI container at token `"AgenticStore"`."""

    def __init__(self, env: Env) -> None:
        # Constructor is cheap — actual psycopg pool open happens in init().
        self._dsn = _normalize_dsn(env.database_url)
        self._pool: AsyncConnectionPool | None = None
        self._store: AsyncPostgresStore | None = None
        self._checkpointer: AsyncPostgresSaver | None = None

    async def init(self) -> None:
        """Open the pool, build the LangGraph primitives, and run their
        `setup()` to create the underlying tables. Call once at startup."""
        # `open=False` so we can `await pool.open()` in async context.
        self._pool = AsyncConnectionPool(
            self._dsn,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        await self._pool.open()

        self._store = AsyncPostgresStore(self._pool)
        await self._store.setup()  # CREATE TABLE IF NOT EXISTS for the store

        self._checkpointer = AsyncPostgresSaver(self._pool)
        await self._checkpointer.setup()  # same for checkpoint tables

    async def dispose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._store = None
            self._checkpointer = None

    @property
    def store(self) -> AsyncPostgresStore:
        if self._store is None:
            raise RuntimeError("AgenticStore.init() must be called first")
        return self._store

    @property
    def checkpointer(self) -> AsyncPostgresSaver:
        if self._checkpointer is None:
            raise RuntimeError("AgenticStore.init() must be called first")
        return self._checkpointer

    @property
    def pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("AgenticStore.init() must be called first")
        return self._pool


def _normalize_dsn(dsn: str) -> str:
    """psycopg accepts `postgres://` and `postgresql://` natively, but doesn't
    take SQLAlchemy-style `postgresql+asyncpg://`. Strip that driver suffix if
    it sneaked in from an older env file."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return dsn
