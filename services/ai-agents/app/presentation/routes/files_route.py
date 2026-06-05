"""Mounted at /v1. Endpoints: POST /files, GET /files/{id}, DELETE /files/{id}."""

from __future__ import annotations

from fastapi import status

from ..controllers.files_controller import FileResponse, FilesController
from .base_route import BaseRoute


class FilesRoute(BaseRoute):
    path = ""

    def __init__(self, controller: FilesController) -> None:
        self._controller = controller
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_route(
            "/files",
            self._controller.upload_file,
            methods=["POST"],
            response_model=FileResponse,
            status_code=status.HTTP_201_CREATED,
            tags=["files"],
        )
        self.router.add_api_route(
            "/files/{file_id}",
            self._controller.get_file,
            methods=["GET"],
            response_model=FileResponse,
            tags=["files"],
        )
        self.router.add_api_route(
            "/files/{file_id}",
            self._controller.delete_file,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
            tags=["files"],
        )
