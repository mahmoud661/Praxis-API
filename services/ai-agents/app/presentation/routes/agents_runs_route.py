"""Mounted at /v1/agents/runs. One endpoint today (`GET /active`) — the
real-time updates go through the notifications WS."""

from __future__ import annotations

from ..controllers.agents_runs_controller import (
    ActiveRunsResponse,
    AgentsRunsController,
)
from .base_route import BaseRoute


class AgentsRunsRoute(BaseRoute):
    path = "/agents/runs"

    def __init__(self, controller: AgentsRunsController) -> None:
        # Container resolves `controller: AgentsRunsController` from token
        # "AgentsRunsController".
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "/active",
            self._controller.active,
            methods=["GET"],
            response_model=ActiveRunsResponse,
            tags=["agents"],
        )
