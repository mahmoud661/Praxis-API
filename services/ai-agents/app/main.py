from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .composition_root import build_container
# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so we don't import any tracing setup here.
from .infrastructure.persistence.migrations import run_migrations
from .presentation.http.controllers.agents_controller import (
    make_router as make_agents_router,
)
from .presentation.http.controllers.health_controller import (
    make_router as make_health_router,
)


def create_app() -> FastAPI:
    container = build_container()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Boot order: DB pool -> migrations -> Kafka producer -> consumer.
        await container.pg.init()
        await run_migrations(container.pg)
        await container.publisher.start()
        container.consumer.on(
            "UserRegistered", container.provision_default_agent.execute
        )
        await container.consumer.start(["auth.events.v1"])
        container.logger.info("service.ready", service=container.env.service_name)
        try:
            yield
        finally:
            await container.consumer.stop()
            await container.publisher.stop()
            await container.pg.close()

    app = FastAPI(title="ai-agents-service", lifespan=lifespan)

    app.include_router(make_health_router(conn=container.pg))
    app.include_router(
        make_agents_router(
            create=container.create_agent,
            list_for_user=container.list_user_agents,
        )
    )
    return app


app = create_app()
