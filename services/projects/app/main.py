from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so no tracing setup here.
from .presentation.di.container import mount_routes, register_dependencies


def create_app() -> FastAPI:
    container = register_dependencies()
    logger = container.resolve("Logger")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Initialize database tables on startup. SQLAlchemy `create_all`
        # is idempotent — it skips tables that already exist. This keeps
        # the service self-contained without requiring a separate migration
        # step in dev; for production, run Alembic migrations in a
        # pre-deployment hook and this becomes a harmless no-op.
        from .infrastructure.database.base import Base, async_engine

        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("database.ready")
        logger.info("service.ready", service="projects-service")

        try:
            yield
        finally:
            # Dispose the connection pool cleanly on shutdown so Postgres
            # does not see abrupt client disconnects in its logs.
            await async_engine.dispose()
            logger.info("service.shutdown")

    app = FastAPI(title="projects-service", lifespan=lifespan)

    # NOTE: No CORS middleware here. This service is internal-only — the
    # gateway is the single CORS authority and terminates all browser CORS.
    # A downstream CORSMiddleware with allow_origins=["*"] emits
    # `Access-Control-Allow-Origin: *`, which the proxy pipes over the
    # gateway's per-origin header; combined with credentialed requests the
    # browser rejects it. Matches ai-agents / memory (neither adds CORS).

    mount_routes(app, container)
    return app


app = create_app()
