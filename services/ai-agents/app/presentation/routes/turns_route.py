"""Mounted at /v1/threads/{thread_id}/turns. Two POST endpoints — retry
and edit — that operate on a specific past message in the thread."""

from __future__ import annotations

from fastapi import status

from ..controllers.turns_controller import TurnsController
from .base_route import BaseRoute


class TurnsRoute(BaseRoute):
    path = "/threads"

    def __init__(self, controller: TurnsController) -> None:
        # Container resolves `controller: TurnsController` from token
        # "TurnsController".
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "/{thread_id}/turns/retry",
            self._controller.retry,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_model=None,
            tags=["turns"],
        )
        self.router.add_api_route(
            "/{thread_id}/turns/edit",
            self._controller.edit,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_model=None,
            tags=["turns"],
        )
