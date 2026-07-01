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
        # No async resources to open in this service (E2B SDK is sync,
        # wrapped in run_in_executor).  The lifespan hook is kept so we
        # have a natural place to add teardown logic later (e.g. killing
        # all active sandboxes on graceful shutdown).
        yield
        # Teardown: nothing to drain right now — E2B sandboxes continue
        # running on E2B's side after the service exits.  Add explicit
        # kill-all here if the deployment policy changes.

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
