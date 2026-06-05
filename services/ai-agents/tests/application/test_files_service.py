"""Tests for `FilesService`.

Tests run against `InMemoryFileStorage` so they don't touch disk;
metadata uses a `_FakeAgenticStore` that mimics the LangGraph
k/v-store API (`aget` / `aput` / `adelete`) just enough for the tests.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from app.application.services._errors import (
    FileNotFoundError,
    FileTooLargeError,
    UnsupportedMimeTypeError,
)
from app.application.services.files_service import MAX_FILE_BYTES, FilesService
from app.infrastructure.files.file_storage import InMemoryFileStorage


class _FakeLogger:
    def info(self, *a: object, **kw: object) -> None: ...
    def warning(self, *a: object, **kw: object) -> None: ...
    def error(self, *a: object, **kw: object) -> None: ...
    def debug(self, *a: object, **kw: object) -> None: ...


@dataclass
class _FakeItem:
    key: str
    value: dict[str, Any]


class _FakeStore:
    """Tiny stand-in for the LangGraph k/v store. Tracks puts/gets in a
    plain dict per namespace."""

    def __init__(self) -> None:
        self._data: dict[tuple, dict[str, dict[str, Any]]] = {}

    async def aput(self, namespace: tuple, key: str, value: dict[str, Any]) -> None:
        self._data.setdefault(namespace, {})[key] = value

    async def aget(self, namespace: tuple, key: str) -> _FakeItem | None:
        ns = self._data.get(namespace, {})
        if key not in ns:
            return None
        return _FakeItem(key=key, value=ns[key])

    async def adelete(self, namespace: tuple, key: str) -> None:
        self._data.get(namespace, {}).pop(key, None)


class _FakeAgenticStore:
    """Wrapper to match `AgenticStore.store` attribute access in
    `FilesService`."""

    def __init__(self) -> None:
        self.store = _FakeStore()


def _service() -> FilesService:
    return FilesService(
        file_storage=InMemoryFileStorage(),
        agentic_store=_FakeAgenticStore(),  # type: ignore[arg-type]
        logger=_FakeLogger(),
    )


# ---- upload ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_persists_bytes_and_returns_view():
    svc = _service()
    view = await svc.upload(
        owner_id="u1",
        filename="hello.txt",
        mime_type="text/plain",
        data=b"hello",
    )
    assert view.owner_id == "u1"
    assert view.filename == "hello.txt"
    assert view.mime_type == "text/plain"
    assert view.size_bytes == 5
    # Generated id is a hex UUID (32 chars, no dashes).
    assert len(view.id) == 32

    # Bytes round-trip via read_bytes with the same owner.
    body = await svc.read_bytes(file_id=view.id, owner_id="u1")
    assert body == b"hello"


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime_type():
    svc = _service()
    with pytest.raises(UnsupportedMimeTypeError) as exc:
        await svc.upload(
            owner_id="u1",
            filename="bad.exe",
            mime_type="application/x-executable",
            data=b"\x7fELF",
        )
    assert exc.value.mime == "application/x-executable"


@pytest.mark.asyncio
async def test_upload_rejects_oversized_payload():
    svc = _service()
    payload = b"x" * (MAX_FILE_BYTES + 1)
    with pytest.raises(FileTooLargeError) as exc:
        await svc.upload(
            owner_id="u1",
            filename="big.txt",
            mime_type="text/plain",
            data=payload,
        )
    assert exc.value.max_size == MAX_FILE_BYTES
    assert exc.value.size == MAX_FILE_BYTES + 1


# ---- get / ownership -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_owner_returns_view():
    svc = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    fetched = await svc.get(file_id=view.id, owner_id="u1")
    assert fetched.id == view.id


@pytest.mark.asyncio
async def test_get_with_wrong_owner_raises_not_found():
    # Cross-user access doesn't leak existence — same error as a
    # nonexistent id.
    svc = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id=view.id, owner_id="u2")


@pytest.mark.asyncio
async def test_get_missing_file_raises_not_found():
    svc = _service()
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id="ghost-id", owner_id="u1")


@pytest.mark.asyncio
async def test_read_bytes_with_wrong_owner_raises_not_found():
    svc = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.read_bytes(file_id=view.id, owner_id="u2")


# ---- delete ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_metadata_and_bytes():
    svc = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    await svc.delete(file_id=view.id, owner_id="u1")
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id=view.id, owner_id="u1")


@pytest.mark.asyncio
async def test_delete_with_wrong_owner_raises_not_found():
    svc = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.delete(file_id=view.id, owner_id="u2")
    # Original owner can still see it — the failed delete was a no-op.
    assert (await svc.get(file_id=view.id, owner_id="u1")).id == view.id
