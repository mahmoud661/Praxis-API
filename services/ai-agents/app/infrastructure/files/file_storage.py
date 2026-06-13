"""
File storage abstraction.

Three backends are mapped to one interface so the upload endpoint stays
agnostic about where bytes actually live:

  - `LocalFileStorage`        on-disk under a configurable root dir
  - `InMemoryFileStorage`     dict-backed; for tests
  - `S3FileStorage` (stub)    interface ready, impl deferred until we
                              need multi-instance file serving — adds
                              `aioboto3` to pyproject and resolves with a
                              presigned-URL handoff. The stub raises a
                              clear error at construction so a misconfig
                              fails loudly.

Switched via the `FILES_STORAGE_BACKEND` env var read in `register_dependencies`.
The interface is bytes-in / bytes-out — content addressing (per-user
scoping, MIME enforcement, size caps) lives one layer up in
`FilesService` so swapping backends doesn't ripple to validation.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Protocol

from ...domain.ports.logger import Logger


class FileNotFoundInStorage(Exception):
    """Raised when `read` / `delete` target a missing file id. Carries
    the id so the controller can branch on it for 404."""

    def __init__(self, file_id: str) -> None:
        super().__init__(f"file not found in storage: {file_id!r}")
        self.file_id = file_id


class IFileStorage(Protocol):
    """Backend-agnostic bytes store.

    `file_id` is opaque to the storage — the caller (FilesService)
    generates it (UUID + per-user namespace prefix) and gives it back
    on read/delete. The storage never inspects it; it just uses it as
    the key.
    """

    async def write(self, file_id: str, data: bytes) -> None:
        """Persist `data` under `file_id`. Idempotent — re-writing the
        same id replaces the bytes (caller is responsible for not
        re-using ids)."""

    async def read(self, file_id: str) -> bytes:
        """Return the bytes previously written for `file_id`. Raises
        `FileNotFoundInStorage` if the id is unknown."""

    async def delete(self, file_id: str) -> None:
        """Remove the bytes for `file_id`. Idempotent — deleting an
        unknown id is a no-op (no FileNotFoundInStorage)."""


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class LocalFileStorage:
    """Writes to a directory on the local filesystem.

    File id maps to `<root>/<file_id[:2]>/<file_id>` so the storage
    doesn't accumulate millions of files under one directory (ext4
    handles it, but listing/maintenance gets painful). The 2-char
    sharding gives 256 sub-dirs which is plenty for any single pod.

    Pass a path that's volume-mounted in production so files survive
    pod restarts. In docker-compose:
        volumes:
          - praxis-files:/var/lib/praxis/files
    """

    def __init__(self, root_dir: Path | str, logger: Logger) -> None:
        self._root = Path(root_dir)
        self._logger = logger
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, file_id: str) -> Path:
        # Guard against path traversal: refuse anything with a slash or
        # ".." segment. File ids are UUIDs in practice (FilesService
        # generates them) but we don't trust callers.
        if "/" in file_id or "\\" in file_id or ".." in file_id:
            raise ValueError(f"invalid file id: {file_id!r}")
        return self._root / file_id[:2] / file_id

    async def write(self, file_id: str, data: bytes) -> None:
        path = self._path(file_id)
        # `mkdir` + `write_bytes` are blocking — run in the default
        # executor so the event loop stays responsive on large writes.
        await asyncio.to_thread(_write_file_sync, path, data)
        self._logger.info(
            "file_storage.write", backend="local", file_id=file_id,
            size=len(data),
        )

    async def read(self, file_id: str) -> bytes:
        path = self._path(file_id)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise FileNotFoundInStorage(file_id) from exc

    async def delete(self, file_id: str) -> None:
        path = self._path(file_id)
        try:
            await asyncio.to_thread(os.remove, path)
        except FileNotFoundError:
            # Idempotent per the protocol contract.
            return
        self._logger.info(
            "file_storage.delete", backend="local", file_id=file_id
        )


def _write_file_sync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `wb` truncates — matches the "idempotent re-write" contract.
    with path.open("wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# In-memory backend (tests + dev smoke)
# ---------------------------------------------------------------------------


class InMemoryFileStorage:
    """Dict-backed bytes store. Useful for unit tests and short-lived
    dev runs. Forgets everything on process restart — never use in
    anything resembling production."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def write(self, file_id: str, data: bytes) -> None:
        self._files[file_id] = data

    async def read(self, file_id: str) -> bytes:
        if file_id not in self._files:
            raise FileNotFoundInStorage(file_id)
        return self._files[file_id]

    async def delete(self, file_id: str) -> None:
        self._files.pop(file_id, None)


# ---------------------------------------------------------------------------
# S3-compatible backend — interface placeholder
# ---------------------------------------------------------------------------


class S3FileStorage:
    """S3 / S3-compatible (MinIO) backend.

    Constructor raises immediately so a `FILES_STORAGE_BACKEND=s3`
    misconfiguration fails fast at boot, not when the first user tries
    to upload. Wire up `aioboto3` (or the smaller `aiobotocore`) in a
    follow-up PR — the interface contract is what callers code
    against, so the swap is mechanical.

    Sketch of the real impl:
        async def write(self, file_id, data):
            async with self._client.client("s3", ...) as s3:
                await s3.put_object(Bucket=self._bucket, Key=file_id, Body=data)
    """

    def __init__(self, *, bucket: str, endpoint_url: str | None = None) -> None:
        del bucket, endpoint_url
        raise NotImplementedError(
            "S3FileStorage is interface-only. Add `aioboto3` to "
            "pyproject.toml and implement write/read/delete against it. "
            "See infrastructure/files/file_storage.py for the contract."
        )

    async def write(self, file_id: str, data: bytes) -> None:
        del file_id, data
        raise NotImplementedError

    async def read(self, file_id: str) -> bytes:
        del file_id
        raise NotImplementedError

    async def delete(self, file_id: str) -> None:
        del file_id
        raise NotImplementedError
