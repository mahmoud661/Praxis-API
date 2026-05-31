"""
Every route module extends `BaseRoute`. The AppServer (in `main.py`) globs
this folder, instantiates each subclass via the DI container (so the route's
controller dependency is injected), and mounts the router at `path`.

Mirrors the TS auth-service `base.route.ts` pattern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import APIRouter


class BaseRoute(ABC):
    path: str = ""

    def __init__(self) -> None:
        self.router: APIRouter = APIRouter()
        self._init_routes()

    @abstractmethod
    def _init_routes(self) -> None:
        """Subclass wires endpoints onto `self.router`."""
        raise NotImplementedError
