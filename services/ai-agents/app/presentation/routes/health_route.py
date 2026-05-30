"""Mounted at root: /healthz (liveness) and /readyz (readiness)."""

from __future__ import annotations

from ..controllers.health_controller import HealthController
from .base_route import BaseRoute


class HealthRoute(BaseRoute):
    path = ""

    def __init__(self, controller: HealthController) -> None:
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "/healthz", self._controller.liveness, methods=["GET"], tags=["health"]
        )
        self.router.add_api_route(
            "/readyz", self._controller.readiness, methods=["GET"], tags=["health"]
        )
