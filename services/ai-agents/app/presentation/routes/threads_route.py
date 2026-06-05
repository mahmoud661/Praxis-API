"""Mounted at /v1/threads. CRUD-ish endpoints for the sidebar + history."""

from __future__ import annotations

from fastapi import status

from ..controllers.threads_controller import (
    HistoryResponse,
    ThreadListResponse,
    ThreadResponse,
    ThreadsController,
)
from .base_route import BaseRoute


class ThreadsRoute(BaseRoute):
    path = "/threads"

    def __init__(self, controller: ThreadsController) -> None:
        # Container resolves `controller: ThreadsController` from token
        # "ThreadsController".
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "",
            self._controller.list_threads,
            methods=["GET"],
            response_model=ThreadListResponse,
            tags=["threads"],
        )
        self.router.add_api_route(
            "",
            self._controller.create_thread,
            methods=["POST"],
            response_model=ThreadResponse,
            status_code=status.HTTP_201_CREATED,
            tags=["threads"],
        )
        self.router.add_api_route(
            "/{thread_id}",
            self._controller.get_thread,
            methods=["GET"],
            response_model=ThreadResponse,
            tags=["threads"],
        )
        self.router.add_api_route(
            "/{thread_id}",
            self._controller.delete_thread,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
            tags=["threads"],
        )
        self.router.add_api_route(
            "/{thread_id}/config",
            self._controller.update_thread_config,
            methods=["PATCH"],
            response_model=ThreadResponse,
            tags=["threads"],
        )
        self.router.add_api_route(
            "/{thread_id}/messages",
            self._controller.thread_history,
            methods=["GET"],
            response_model=HistoryResponse,
            tags=["threads"],
        )
