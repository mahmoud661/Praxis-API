from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import APIRouter


class BaseRoute(ABC):
    """Base class for all route modules.

    Subclasses set `path` (the prefix under which `router` is mounted)
    and populate `router` with their endpoint handlers.  The DI container
    discovers subclasses automatically via a filesystem glob over the
    `routes/` package and mounts each one on the FastAPI application.
    """

    path: str = ""
    router: APIRouter

    @abstractmethod
    def __init__(self) -> None:  # pragma: no cover
        ...
