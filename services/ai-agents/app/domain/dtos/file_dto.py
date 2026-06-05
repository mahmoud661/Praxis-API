"""DTOs for the `/v1/files/*` endpoints.

Files are PER-USER. Two ids matter:

  - `id` — what the frontend references on the next chat send. UUID4,
    generated server-side at upload time.
  - `owner_id` — the user who uploaded. Every read/delete operation
    checks ownership before doing anything; cross-user access raises
    `FileNotFoundError` (we don't leak existence).

Files are stored in the `IFileStorage` backend (local / S3 / in-mem).
Metadata (mime, size, owner, original filename, created_at) lives in
the LangGraph k/v store under namespace `("files",)` so we don't
introduce a second persistence layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileView:
    """One uploaded file's metadata as returned by the API."""

    id: str
    owner_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: str  # ISO-8601
