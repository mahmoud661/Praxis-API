from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so no tracing setup here.
from .presentation.di.container import mount_routes, register_dependencies


def create_app() -> FastAPI:
    # Wire the container once. AgenticStore is registered but NOT yet
    # connected — that happens in the lifespan below.
    container = register_dependencies()
    logger = container.resolve("Logger")
    env = container.resolve("Env")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Boot order: open the agentic store (LangGraph Postgres Store +
        # Checkpointer) -> Kafka producer -> consumer.
        #
        # `AgenticStore.init()` opens the psycopg pool, then runs `setup()`
        # on both the Store and the Checkpointer — that's the entire
        # migration system. No Alembic, no SQLAlchemy.
        agentic_store = container.resolve("AgenticStore")
        await agentic_store.init()
        logger.info("agentic_store.ready")

        publisher = container.resolve("EventPublisher")
        consumer = container.resolve("EventConsumer")

        await publisher.start()
        # No event handlers registered yet — the service currently has no
        # agent-persistence story, so nothing to do on `UserRegistered` etc.
        # The consumer still starts so the scaffolding is hot; topics it
        # subscribes to are dispatched-on-arrival the moment we wire a
        # handler (e.g. when the LangGraph runtime gets thread endpoints).
        await consumer.start(["auth.events.v1"])
        logger.info("service.ready", service=env.service_name)
        try:
            yield
        finally:
            # Stop accepting new work, then drain in-flight runs so each
            # one's `finally` (Redis cleanup, run.ended notification) runs.
            await consumer.stop()
            await publisher.stop()
            await container.resolve("RunManager").cancel_all()
            # Close the Redis client — opened lazily on first use, so this
            # is a no-op if no agent has run yet.
            await container.resolve("Redis").aclose()
            await agentic_store.dispose()

    app = FastAPI(title="ai-agents-service", lifespan=lifespan)
    # Auto-mount every BaseRoute subclass found in presentation/routes/.
    mount_routes(app, container)
    return app


app = create_app()
