from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# OpenTelemetry is loaded via the `opentelemetry-instrument` CLI wrapper
# in the Dockerfile, so no tracing setup here.
from .presentation.di.container import build_container, mount_routes


def create_app() -> FastAPI:
    container = build_container()
    env = container.env

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=env.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    mount_routes(app, container)
    return app


app = create_app()
