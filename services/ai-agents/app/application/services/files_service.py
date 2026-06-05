"""
FilesService — orchestrates user-scoped file upload + retrieval.

Storage of bytes is delegated to `IFileStorage` (local / S3 / in-memory).
Metadata lives in the LangGraph k/v store under namespace `("files",)`,
keyed by the same file id the storage backend uses. That way one
write = bytes + metadata as a single logical operation.

Validation up here so the storage adapter stays bytes-in-bytes-out:
  - Size cap enforced against `MAX_FILE_BYTES` (32 MiB — matches the
    `CapabilitiesService` default attachment limit, on purpose).
  - MIME allowlist matches what `CapabilitiesService` surfaces under
    `accepts.mime_types`. Keeping the two lists in sync is enforced by
    referencing the same source via `capabilities_service._mimes_for`
    on the multimodal case — but for upload we accept the UNION of
    every modality's MIME types (any registered agent can accept
    these inputs, even if THIS thread's agent can't).

Ownership enforcement: every read/delete checks that the file's
recorded owner matches `owner_id` — cross-user access raises
`FileNotFoundError`. We never leak existence.

Auto-bound to DI token `"IFilesService"` (CapabilitiesService-style:
class name = token).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...domain.dtos.file_dto import FileView
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore
from ...infrastructure.files.file_storage import (
    FileNotFoundInStorage,
    IFileStorage,
)
from ._errors import (
    FileNotFoundError,
    FileTooLargeError,
    UnsupportedMimeTypeError,
)

# Matches the CapabilitiesService default. If you bump this, bump
# `_DEFAULT_MAX_ATTACHMENT_BYTES` in capabilities_service.py too —
# they're the same logical constant from two layers' perspectives.
MAX_FILE_BYTES = 32 * 1024 * 1024

# Union of every modality's MIME types. Keep in sync with
# `_MIME_TYPES_BY_MODALITY` in `capabilities_service.py`. We accept the
# union here (not the active thread's allowlist) because uploads are
# per-USER, not per-thread — a file uploaded for an agent that accepts
# pdfs can be referenced later by another agent that accepts pdfs too.
ACCEPTED_MIME_TYPES: frozenset[str] = frozenset(
    {
        # text
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        # image
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        # pdf
        "application/pdf",
        # audio
        "audio/mpeg",
        "audio/wav",
        "audio/webm",
        # video
        "video/mp4",
        "video/webm",
    }
)

_NAMESPACE = ("files",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FilesService:
    """Auto-bound to the DI token `"IFilesService"`."""

    def __init__(
        self,
        file_storage: IFileStorage,
        agentic_store: AgenticStore,
        logger: Logger,
    ) -> None:
        self._storage = file_storage
        self._agentic = agentic_store
        self._logger = logger

    async def upload(
        self,
        *,
        owner_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> FileView:
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLargeError(len(data), MAX_FILE_BYTES)
        if mime_type not in ACCEPTED_MIME_TYPES:
            raise UnsupportedMimeTypeError(mime_type)

        file_id = uuid4().hex  # no dashes — easier to grep + URL-safe by default
        created_at = _now_iso()
        # Write bytes first so a metadata write failure leaves no
        # dangling pointer. The reverse (metadata first, bytes second)
        # would surface a half-written file to readers.
        await self._storage.write(file_id, data)
        meta = {
            "owner_id": owner_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "created_at": created_at,
        }
        await self._agentic.store.aput(_NAMESPACE, file_id, meta)

        self._logger.info(
            "file.uploaded",
            file_id=file_id,
            owner_id=owner_id,
            mime_type=mime_type,
            size=len(data),
        )
        return FileView(
            id=file_id,
            owner_id=owner_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            created_at=created_at,
        )

    async def get(self, *, file_id: str, owner_id: str) -> FileView:
        view = await self._load(file_id)
        if view is None or view.owner_id != owner_id:
            raise FileNotFoundError(file_id)
        return view

    async def read_bytes(self, *, file_id: str, owner_id: str) -> bytes:
        # Resolve metadata FIRST so the ownership check happens before
        # we touch the storage backend (cheaper than reading bytes for
        # a 404'd request).
        await self.get(file_id=file_id, owner_id=owner_id)
        try:
            return await self._storage.read(file_id)
        except FileNotFoundInStorage as exc:
            # Metadata says it exists but storage doesn't — orphan.
            # Treat as a real 404 + log loudly; the metadata should
            # have been pruned alongside the storage delete.
            self._logger.warning(
                "file.metadata_without_storage",
                file_id=file_id,
                owner_id=owner_id,
            )
            raise FileNotFoundError(file_id) from exc

    async def delete(self, *, file_id: str, owner_id: str) -> None:
        # Ownership check first — same "404 on miss-or-foreign" rule.
        await self.get(file_id=file_id, owner_id=owner_id)
        # Delete metadata FIRST so a half-failure leaves orphaned
        # bytes (cheap to clean up later) rather than orphaned
        # metadata (which would 500 on read).
        await self._agentic.store.adelete(_NAMESPACE, file_id)
        await self._storage.delete(file_id)
        self._logger.info(
            "file.deleted", file_id=file_id, owner_id=owner_id
        )

    async def _load(self, file_id: str) -> FileView | None:
        item = await self._agentic.store.aget(_NAMESPACE, file_id)
        if item is None:
            return None
        return _to_view(item.key, item.value)


def _to_view(key: str, value: dict[str, Any]) -> FileView:
    return FileView(
        id=key,
        owner_id=str(value.get("owner_id", "")),
        filename=str(value.get("filename", "")),
        mime_type=str(value.get("mime_type", "application/octet-stream")),
        size_bytes=int(value.get("size_bytes", 0) or 0),
        created_at=str(value.get("created_at", _now_iso())),
    )
