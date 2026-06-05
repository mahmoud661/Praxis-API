"""Mounted at /v1. Single endpoint: GET /capabilities."""

from __future__ import annotations

from ..controllers.capabilities_controller import (
    CapabilitiesController,
    CapabilitiesResponse,
)
from .base_route import BaseRoute


class CapabilitiesRoute(BaseRoute):
    path = ""

    def __init__(self, controller: CapabilitiesController) -> None:
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "/capabilities",
            self._controller.get_capabilities,
            methods=["GET"],
            response_model=CapabilitiesResponse,
            tags=["capabilities"],
        )
