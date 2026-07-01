from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import Base from the domain models so the engine and the ORM share a
# single metadata object.  Any model that imports from `domain.models`
# registers itself with this Base.metadata automatically.
from ...domain.models import Base

# Read the DSN at module import time.  In production the env var is set
# by the container runtime; in tests it is patched before the module is
# first imported (or the service is not instantiated at all).
_DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

# `echo=False` in production — set DATABASE_ECHO=true to enable SQL logging
# during local debugging.
_echo: bool = os.environ.get("DATABASE_ECHO", "false").lower() in ("1", "true", "yes")

async_engine = create_async_engine(
    _DATABASE_URL,
    echo=_echo,
    # Pool sizing.  The service is single-process/single-worker in the
    # current compose setup.  pool_pre_ping keeps the pool healthy across
    # Postgres restarts without requiring a service restart.
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

__all__ = ["async_engine", "AsyncSessionLocal", "Base"]
