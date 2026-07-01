"""
Composition Root
================

Single place that imports concrete adapters together with their tokens and
wires the full dependency graph.  The Container is a minimal string-keyed
singleton registry — the same pattern used by the auth and ai-agents services.

Wiring order (dependencies must be registered before dependents):
  Settings
    -> Logger
    -> AsyncSessionLocal (session factory)
      -> ProjectRepository  (token: "IProjectRepository")
        -> ProjectsService  (token: "IProjectService")
          -> ProjectsController
            -> ProjectsRoute  (auto-mounted)
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, get_type_hints

import structlog

from ...application.projects_service import ProjectsService
from ...infrastructure.config.env import load_settings
from ...infrastructure.database.base import AsyncSessionLocal
from ...infrastructure.database.project_repository import ProjectRepository
from ..controllers.projects_controller import ProjectsController
from ..routes.projects_route import ProjectsRoute as ProjectsRoute  # noqa: F401


class Container:
    """Minimal DI container.  Tokens are strings; instances are cached singletons."""

    def __init__(self) -> None:
        self._bindings: dict[str, Any] = {}

    def register(self, token: str, value: Any) -> None:
        self._bindings[token] = value

    def has(self, token: str) -> bool:
        return token in self._bindings

    def resolve(self, token: str) -> Any:
        if token not in self._bindings:
            raise KeyError(f"No binding registered for {token!r}")
        return self._bindings[token]

    def construct(self, cls: type) -> Any:
        """Instantiate `cls` by reading its `__init__` annotations and
        resolving each parameter from the container by the annotation's
        `__name__`."""
        try:
            hints = get_type_hints(cls.__init__)
        except Exception:  # noqa: BLE001
            hints = {}
        sig = inspect.signature(cls.__init__)
        kwargs: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            anno = hints.get(name)
            if anno is None:
                raise RuntimeError(
                    f"{cls.__name__}.__init__ parameter {name!r} has no "
                    f"resolvable type annotation; the DI container needs one."
                )
            token = getattr(anno, "__name__", str(anno))
            kwargs[name] = self.resolve(token)
        return cls(**kwargs)


def register_dependencies() -> Container:
    """Wire the full dependency graph and return the container."""
    container = Container()

    settings = load_settings()
    container.register("Settings", settings)

    # Structlog logger — structured JSON in production, coloured in dev.
    logger = structlog.get_logger(settings.service_name)
    container.register("Logger", logger)

    # SQLAlchemy async session factory.  The engine is already created in
    # `infrastructure/database/base.py` at import time; we register the
    # factory (not the engine) so downstream classes can open sessions.
    # Token matches the class name so the DI `construct()` lookup works.
    container.register("async_sessionmaker", AsyncSessionLocal)

    # Repository — satisfies IProjectRepository protocol.
    project_repo = ProjectRepository(session_factory=AsyncSessionLocal)
    container.register("IProjectRepository", project_repo)
    container.register("ProjectRepository", project_repo)

    # Application service.
    projects_service = ProjectsService(
        project_repository=project_repo,
        settings=settings,
    )
    container.register("IProjectService", projects_service)
    container.register("ProjectsService", projects_service)

    # Presentation layer.
    projects_controller = ProjectsController(projects_service=projects_service)
    container.register("ProjectsController", projects_controller)

    return container


def mount_routes(app: Any, container: Container) -> None:
    """Instantiate ProjectsRoute (and any future routes) and mount them."""
    from ..routes.base_route import BaseRoute

    # Filesystem glob over presentation/routes/ — mirrors the ai-agents pattern.
    # New routes are picked up automatically; just drop a new .py file in the
    # package and import it at the top of this module with the `as X` re-export
    # form so linters don't flag the import as unused.
    routes_pkg_path = Path(__file__).resolve().parents[1] / "routes"
    logger = container.resolve("Logger")

    for py_file in sorted(routes_pkg_path.glob("*.py")):
        if py_file.name.startswith("_") or py_file.stem == "base_route":
            continue
        mod_name = f"app.presentation.routes.{py_file.stem}"
        module = importlib.import_module(mod_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if not issubclass(obj, BaseRoute) or obj is BaseRoute:
                continue
            route: BaseRoute = container.construct(obj)
            app.include_router(route.router, prefix=route.path)
            logger.info("route.mounted", path=route.path or "/")


__all__ = ["Container", "register_dependencies", "mount_routes"]
