"""REST controller for `/v1/files`. multipart upload + GET metadata +
GET bytes (for image thumbnails on history reload) + DELETE.

The bytes endpoint (`GET /v1/files/{id}/content`) returns the raw file
with the correct `Content-Type` so an `<img src="/v1/files/{id}/content">`
just works. Ownership is enforced exactly the same as metadata — a
cross-user request gets a 404, no existence leak.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, File, HTTPException, Response, UploadFile, status
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


def content_disposition_for(mime_type: str, filename: str) -> str:
    """Build a header-safe Content-Disposition for the bytes endpoint.

    Images render inline (`<img>` thumbnails rely on it); everything else
    downloads as an attachment so the browser never interprets uploaded
    bytes as a document in our origin. The filename is user input — the
    ASCII fallback replaces anything that could break out of the quoted
    string (control chars, quotes, backslashes), and the RFC 5987
    `filename*` form carries the exact name percent-encoded.
    """
    kind = "inline" if mime_type.startswith("image/") else "attachment"
    fallback = (
        "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
        or "file"
    )
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


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

    async def get_file_content(
        self,
        file_id: str,
        user_id: str = Depends(current_user_id),
    ) -> Response:
        """Return the raw bytes with the file's recorded MIME type so
        the browser can render image thumbnails via an `<img>` tag
        after a history reload (the original Blob URL from the upload
        session is gone). Same ownership semantics as `get_file` —
        cross-user access returns 404."""
        try:
            view = await self._service.get(file_id=file_id, owner_id=user_id)
            data = await self._service.read_bytes(
                file_id=file_id, owner_id=user_id
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        # Cache for 1h — file bytes are immutable per id (re-upload
        # gets a new id), so the browser can safely reuse. nosniff pins
        # the recorded MIME type; non-images download instead of
        # rendering in-origin (see content_disposition_for).
        return Response(
            content=data,
            media_type=view.mime_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": content_disposition_for(
                    view.mime_type, view.filename
                ),
            },
        )
