from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so no tracing setup here.
from .presentation.di.container import mount_routes, register_dependencies


def create_app() -> FastAPI:
    container = register_dependencies()
    logger = container.resolve("Logger")
    settings = container.resolve("Settings")

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

    # CORS — origins come from settings so the compose / k8s overlay can
    # lock them down for production without touching the source.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    mount_routes(app, container)
    return app


app = create_app()
