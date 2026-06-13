"""Tests for the storage backends. Covers `LocalFileStorage` (real
filesystem via pytest's `tmp_path`) and `InMemoryFileStorage`.
`S3FileStorage` is interface-only today; we just verify its constructor
fails with the documented error."""

from pathlib import Path

import pytest

from app.infrastructure.files.file_storage import (
    FileNotFoundInStorage,
    InMemoryFileStorage,
    LocalFileStorage,
    S3FileStorage,
)


class _FakeLogger:
    def info(self, *a: object, **kw: object) -> None: pass
    def warning(self, *a: object, **kw: object) -> None: pass
    def error(self, *a: object, **kw: object) -> None: pass
    def debug(self, *a: object, **kw: object) -> None: pass


# ---- LocalFileStorage ------------------------------------------------------


@pytest.mark.asyncio
async def test_local_write_then_read_roundtrip(tmp_path: Path):
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    await storage.write("abc123", b"hello world")
    assert await storage.read("abc123") == b"hello world"


@pytest.mark.asyncio
async def test_local_write_is_idempotent_replace(tmp_path: Path):
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    await storage.write("abc123", b"first")
    await storage.write("abc123", b"second")
    assert await storage.read("abc123") == b"second"


@pytest.mark.asyncio
async def test_local_read_missing_raises_file_not_found(tmp_path: Path):
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    with pytest.raises(FileNotFoundInStorage) as exc:
        await storage.read("ghost-id")
    assert exc.value.file_id == "ghost-id"


@pytest.mark.asyncio
async def test_local_delete_is_idempotent(tmp_path: Path):
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    # Delete a never-existed id — should be a no-op, no exception.
    await storage.delete("ghost-id")

    await storage.write("real-id", b"data")
    await storage.delete("real-id")
    with pytest.raises(FileNotFoundInStorage):
        await storage.read("real-id")


@pytest.mark.asyncio
async def test_local_shards_into_subdirs(tmp_path: Path):
    """Verifies the 2-char shard layout — files don't pile up under one dir."""
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    await storage.write("abxyz", b"data")
    assert (tmp_path / "ab" / "abxyz").exists()


def test_local_rejects_path_traversal_file_id(tmp_path: Path):
    storage = LocalFileStorage(tmp_path, _FakeLogger())
    with pytest.raises(ValueError, match="invalid file id"):
        storage._path("../escape")  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="invalid file id"):
        storage._path("sub/dir")  # type: ignore[attr-defined]


# ---- InMemoryFileStorage ---------------------------------------------------


@pytest.mark.asyncio
async def test_inmem_roundtrip():
    storage = InMemoryFileStorage()
    await storage.write("k", b"v")
    assert await storage.read("k") == b"v"


@pytest.mark.asyncio
async def test_inmem_read_missing_raises():
    storage = InMemoryFileStorage()
    with pytest.raises(FileNotFoundInStorage):
        await storage.read("ghost")


@pytest.mark.asyncio
async def test_inmem_delete_is_idempotent():
    storage = InMemoryFileStorage()
    await storage.delete("never-existed")  # no exception


# ---- S3FileStorage (placeholder) ------------------------------------------


def test_s3_constructor_raises_with_actionable_message():
    with pytest.raises(NotImplementedError) as exc:
        S3FileStorage(bucket="praxis-files")
    msg = str(exc.value)
    assert "aioboto3" in msg
    assert "file_storage.py" in msg
