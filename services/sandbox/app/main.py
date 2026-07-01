from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so no tracing setup here.
from .presentation.di.container import build_container, mount_routes


def create_app() -> FastAPI:
    container = build_container()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Graceful shutdown: E2B client kills all active cloud sandboxes so
        # billing stops; local Docker client closes the httpx connection pool.
        await container.service.shutdown()

    app = FastAPI(
        title="sandbox-service",
        description="E2B Desktop sandbox lifecycle management and VNC stream proxying.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # NOTE: No CORS middleware here. This service is internal-only — the
    # gateway is the single CORS authority and terminates all browser CORS.
    # A downstream CORSMiddleware with allow_origins=["*"] emits
    # `Access-Control-Allow-Origin: *`, which the proxy pipes over the
    # gateway's per-origin header; with credentialed requests the browser
    # rejects it. Matches ai-agents / memory / projects (none add CORS).

    mount_routes(app, container)
    return app


app = create_app()
