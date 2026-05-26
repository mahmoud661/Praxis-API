from __future__ import annotations

import asyncpg


class PostgresConnection:
    """Owns the asyncpg pool. The composition root creates one of these
    and hands it to repository adapters."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def dsn(self) -> str:
        return self._dsn

    async def init(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresConnection not initialized")
        return self._pool
