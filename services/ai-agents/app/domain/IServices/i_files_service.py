"""DI token `"IFilesService"` (impl class `FilesService`)."""

from __future__ import annotations

from typing import Protocol

from ..dtos.file_dto import FileView


class IFilesService(Protocol):
    async def upload(
        self,
        *,
        owner_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> FileView:
        """Persist `data` and its metadata. Validates size + MIME
        against the platform's accepted set; out-of-bounds raises
        `FileTooLargeError` or `UnsupportedMimeTypeError`."""

    async def get(self, *, file_id: str, owner_id: str) -> FileView:
        """Single-file metadata fetch with ownership enforcement.
        Cross-user access raises `FileNotFoundError` — we don't leak
        existence to other users."""

    async def read_bytes(self, *, file_id: str, owner_id: str) -> bytes:
        """Return the file's bytes. Same ownership semantics as `get`."""

    async def delete(self, *, file_id: str, owner_id: str) -> None:
        """Drop the metadata + storage entry. Same ownership semantics
        as `get`. Idempotent on the storage side."""
