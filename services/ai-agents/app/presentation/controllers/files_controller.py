"""REST controller for `/v1/files`. multipart upload + GET metadata +
DELETE. The bytes-back read endpoint isn't shipped yet (frontend sends
attachments by id, the agent runtime loads bytes server-side); add a
`GET /v1/files/{id}/raw` later if a user-visible download surface
appears."""

from __future__ import annotations

from fastapi import Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ...application.services._errors import (
    FileNotFoundError,
    FileTooLargeError,
    UnsupportedMimeTypeError,
)
from ...domain.dtos.file_dto import FileView
from ...domain.IServices.i_files_service import IFilesService
from ..http.dependencies import current_user_id


class FileResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: str

    @classmethod
    def from_view(cls, f: FileView) -> "FileResponse":
        return cls(
            id=f.id,
            filename=f.filename,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
            created_at=f.created_at,
        )


class FilesController:
    def __init__(self, service: IFilesService) -> None:
        self._service = service

    async def upload_file(
        self,
        file: UploadFile = File(...),
        user_id: str = Depends(current_user_id),
    ) -> FileResponse:
        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail={"error": "MISSING_FILENAME"},
            )
        data = await file.read()
        try:
            view = await self._service.upload(
                owner_id=user_id,
                filename=file.filename,
                mime_type=file.content_type or "application/octet-stream",
                data=data,
            )
        except FileTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "error": "FILE_TOO_LARGE",
                    "max_bytes": exc.max_size,
                    "received_bytes": exc.size,
                },
            )
        except UnsupportedMimeTypeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={
                    "error": "UNSUPPORTED_MIME_TYPE",
                    "mime_type": exc.mime,
                },
            )
        return FileResponse.from_view(view)

    async def get_file(
        self,
        file_id: str,
        user_id: str = Depends(current_user_id),
    ) -> FileResponse:
        try:
            view = await self._service.get(file_id=file_id, owner_id=user_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        return FileResponse.from_view(view)

    async def delete_file(
        self,
        file_id: str,
        user_id: str = Depends(current_user_id),
    ):
        # No return type annotation — see threads_controller.delete_thread
        # for why (204 + Pydantic response model don't mix).
        try:
            await self._service.delete(file_id=file_id, owner_id=user_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
