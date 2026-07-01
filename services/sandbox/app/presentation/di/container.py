"""
Composition Root
================

Single place that couples concrete types together.  The rest of the codebase
depends on protocols / abstract types; only this module imports both sides.

Wiring order:
  Env
  -> E2BSandboxClient  (infrastructure adapter)
  -> SandboxService    (application layer)
  -> router            (presentation layer, via sandbox_route._make_router)
"""

from __future__ import annotations

from fastapi import FastAPI

from ...application.sandbox_service import SandboxService
from ...domain.ports.i_sandbox_client import ISandboxClient
from ...infrastructure.config.env import Env, load_env
from ..routes.sandbox_route import _make_router


def _build_client(env: Env) -> ISandboxClient:
    """Pick the sandbox driver from config. Imports are lazy so the E2B
    SDK isn't loaded (nor its api-key requirement felt) when running the
    local provider, and vice versa."""
    if env.sandbox_provider == "e2b":
        from ...infrastructure.e2b.e2b_client import E2BSandboxClient

        return E2BSandboxClient(env)
    from ...infrastructure.local.local_docker_client import LocalDockerSandboxClient

    return LocalDockerSandboxClient(env)


class Container:
    """Minimal synchronous DI container.  All singletons; no lazy proxies."""

    def __init__(self) -> None:
        self._env: Env = load_env()
        self._client: ISandboxClient = _build_client(self._env)
        self._service: SandboxService = SandboxService(self._client, self._env)

    @property
    def env(self) -> Env:
        return self._env

    @property
    def service(self) -> SandboxService:
        return self._service


def build_container() -> Container:
    """Instantiate and wire the full dependency graph."""
    return Container()


def mount_routes(app: FastAPI, container: Container) -> None:
    """Attach the sandbox router to the FastAPI application."""
    router = _make_router(container.service)
    app.include_router(router)


__all__ = ["Container", "build_container", "mount_routes"]
