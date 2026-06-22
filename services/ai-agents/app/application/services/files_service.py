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

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...domain.dtos.file_dto import FileView
from ...domain.IServices.i_knowledge_service import IKnowledgeService
from ...domain.ports.i_memory_client import IMemoryClient
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
        knowledge_service: IKnowledgeService,
        memory_client: IMemoryClient,
        logger: Logger,
    ) -> None:
        self._storage = file_storage
        self._agentic = agentic_store
        self._knowledge = knowledge_service
        self._memory = memory_client
        self._logger = logger
        # Strong references to in-flight fire-and-forget ingestion
        # tasks. asyncio only holds a WEAK reference to a bare
        # create_task result, so without this the GC can collect a task
        # mid-await and ingestion silently never finishes. Each task
        # removes itself on completion.
        self._ingest_tasks: set[asyncio.Task[None]] = set()

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
        # Register in knowledge graph — fire-and-forget, strong reference kept so GC
        # can't collect the task before it completes (same pattern as _ingest_safely).
        _ptask = asyncio.create_task(self._provision_in_graph(
            file_id=file_id,
            owner_id=owner_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
        ))
        self._ingest_tasks.add(_ptask)
        _ptask.add_done_callback(self._ingest_tasks.discard)
        # Fire-and-forget ingestion into the knowledge base. We don't
        # block the upload response on it — chunking + embedding can
        # take a few seconds for a big PDF, and the user shouldn't wait
        # to see the file in their attachment list. Failures are logged
        # but don't bubble back to the caller; the file is still
        # readable via `read_attachment` either way.
        #
        # NOT a TaskGroup or shared background scheduler — those are
        # follow-up work. asyncio.create_task is fine for v1 because
        # the task lifetime is bounded by the request loop, and
        # KnowledgeService is fully async without external blocking.
        # We DO keep a strong reference (see `_ingest_tasks`) and clear
        # it on completion so the GC can't reap the task mid-flight.
        task = asyncio.create_task(
            self._ingest_safely(
                owner_id=owner_id,
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                data=data,
            )
        )
        self._ingest_tasks.add(task)
        task.add_done_callback(self._ingest_tasks.discard)

        return FileView(
            id=file_id,
            owner_id=owner_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            created_at=created_at,
        )

    async def _provision_in_graph(
        self,
        *,
        file_id: str,
        owner_id: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> None:
        entity_type = "image" if mime_type.startswith("image/") else "attachment"
        size_kb = size_bytes // 1024 or 1
        try:
            await self._memory.provision_node(
                type=entity_type,
                id=file_id,
                name=filename,
                owner_id=owner_id,
                summary=f"{mime_type} · {size_kb} KB",
            )
        except Exception:  # noqa: BLE001
            self._logger.warning("file.graph_provision_failed", file_id=file_id)

    async def _ingest_safely(
        self,
        *,
        owner_id: str,
        file_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> None:
        try:
            await self._knowledge.ingest_file(
                owner_id=owner_id,
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                data=data,
            )
        except Exception as exc:  # noqa: BLE001 — background task swallows
            self._logger.error(
                "file.ingest_failed",
                file_id=file_id,
                owner_id=owner_id,
                error=repr(exc),
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
        # Drop the vector-store chunks too — these would otherwise
        # outlive their source. Cleanup is best-effort: vector-store
        # outage shouldn't keep a user from deleting their file.
        try:
            await self._knowledge.delete_file_chunks(
                owner_id=owner_id, file_id=file_id
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "file.kb_cleanup_failed",
                file_id=file_id,
                owner_id=owner_id,
                error=repr(exc),
            )
        self._logger.info(
            "file.deleted", file_id=file_id, owner_id=owner_id
        )

    async def set_caption(
        self, *, file_id: str, owner_id: str, caption: str
    ) -> None:
        """Stash a short LLM-generated description on the file's
        metadata. Called from `AttachmentCompactionMiddleware` the
        FIRST time a file is evicted from the model's context — the
        caption then enriches every subsequent eviction stub for that
        file (so the model knows what was there even after the bytes
        leave context).

        Ownership-checked. No-op (logs only) if the file is gone."""
        item = await self._agentic.store.aget(_NAMESPACE, file_id)
        if item is None:
            self._logger.warning(
                "file.caption_set_missing", file_id=file_id, owner_id=owner_id
            )
            return
        meta = dict(item.value or {})
        if str(meta.get("owner_id", "")) != owner_id:
            self._logger.warning(
                "file.caption_set_cross_owner",
                file_id=file_id,
                owner_id=owner_id,
            )
            return
        meta["caption"] = caption
        await self._agentic.store.aput(_NAMESPACE, file_id, meta)

    async def _load(self, file_id: str) -> FileView | None:
        item = await self._agentic.store.aget(_NAMESPACE, file_id)
        if item is None:
            return None
        return _to_view(item.key, item.value)


def _to_view(key: str, value: dict[str, Any]) -> FileView:
    raw_caption = value.get("caption")
    return FileView(
        id=key,
        owner_id=str(value.get("owner_id", "")),
        filename=str(value.get("filename", "")),
        mime_type=str(value.get("mime_type", "application/octet-stream")),
        size_bytes=int(value.get("size_bytes", 0) or 0),
        created_at=str(value.get("created_at", _now_iso())),
        caption=str(raw_caption) if isinstance(raw_caption, str) and raw_caption else None,
    )
