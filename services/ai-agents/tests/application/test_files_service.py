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
    def info(self, *a: object, **kw: object) -> None: pass
    def warning(self, *a: object, **kw: object) -> None: pass
    def error(self, *a: object, **kw: object) -> None: pass
    def debug(self, *a: object, **kw: object) -> None: pass


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


class _FakeKnowledgeService:
    """Stand-in for `IKnowledgeService`. Records ingestion + deletion
    calls so tests can assert the upload → ingestion + delete →
    cleanup wires were exercised."""

    def __init__(self) -> None:
        self.ingested: list[tuple[str, str, str, str, bytes]] = []
        self.deleted: list[tuple[str, str]] = []
        self.raise_on_ingest = False
        self.raise_on_delete = False

    async def ingest_file(
        self,
        *,
        owner_id: str,
        file_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> int:
        if self.raise_on_ingest:
            raise RuntimeError("ingest blew up")
        self.ingested.append((owner_id, file_id, filename, mime_type, data))
        return 1

    async def search(self, **_kwargs: object):  # pragma: no cover
        return []

    async def delete_file_chunks(self, *, owner_id: str, file_id: str) -> None:
        if self.raise_on_delete:
            raise RuntimeError("delete blew up")
        self.deleted.append((owner_id, file_id))


def _service(
    knowledge: _FakeKnowledgeService | None = None,
) -> tuple[FilesService, _FakeKnowledgeService]:
    ks = knowledge or _FakeKnowledgeService()
    svc = FilesService(
        file_storage=InMemoryFileStorage(),
        agentic_store=_FakeAgenticStore(),  # type: ignore[arg-type]
        knowledge_service=ks,
        memory_client=_FakeLogger(),  # type: ignore[arg-type]
        logger=_FakeLogger(),
    )
    return svc, ks


# ---- upload ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_persists_bytes_and_returns_view():
    svc, _ks = _service()
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
    svc, _ks = _service()
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
    svc, _ks = _service()
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
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    fetched = await svc.get(file_id=view.id, owner_id="u1")
    assert fetched.id == view.id


@pytest.mark.asyncio
async def test_get_with_wrong_owner_raises_not_found():
    # Cross-user access doesn't leak existence — same error as a
    # nonexistent id.
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id=view.id, owner_id="u2")


@pytest.mark.asyncio
async def test_get_missing_file_raises_not_found():
    svc, _ks = _service()
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id="ghost-id", owner_id="u1")


@pytest.mark.asyncio
async def test_read_bytes_with_wrong_owner_raises_not_found():
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.read_bytes(file_id=view.id, owner_id="u2")


# ---- delete ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_metadata_and_bytes():
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    await svc.delete(file_id=view.id, owner_id="u1")
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id=view.id, owner_id="u1")


@pytest.mark.asyncio
async def test_delete_with_wrong_owner_raises_not_found():
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    with pytest.raises(FileNotFoundError):
        await svc.delete(file_id=view.id, owner_id="u2")
    # Original owner can still see it — the failed delete was a no-op.
    assert (await svc.get(file_id=view.id, owner_id="u1")).id == view.id


# ---- ingestion + cleanup hooks --------------------------------------------


@pytest.mark.asyncio
async def test_upload_schedules_background_ingestion():
    # Fire-and-forget: upload returns BEFORE ingestion runs. Yielding to
    # the loop once lets the create_task'd ingestion finish so we can
    # assert it was invoked with the right args.
    import asyncio

    svc, ks = _service()
    view = await svc.upload(
        owner_id="u1",
        filename="notes.txt",
        mime_type="text/plain",
        data=b"hello",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)  # one extra tick to let create_task run
    assert ks.ingested == [("u1", view.id, "notes.txt", "text/plain", b"hello")]


@pytest.mark.asyncio
async def test_upload_succeeds_even_if_ingestion_blows_up():
    # The user shouldn't see a 500 on upload because Qdrant is down.
    # The background task swallows + logs; the FileView still returns
    # and the file is readable.
    import asyncio

    ks = _FakeKnowledgeService()
    ks.raise_on_ingest = True
    svc, _ks = _service(ks)
    view = await svc.upload(
        owner_id="u1",
        filename="notes.txt",
        mime_type="text/plain",
        data=b"hello",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Upload succeeded.
    assert view.size_bytes == 5
    # File is reachable via the normal API.
    body = await svc.read_bytes(file_id=view.id, owner_id="u1")
    assert body == b"hello"


@pytest.mark.asyncio
async def test_delete_purges_vector_store_chunks():
    svc, ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    await svc.delete(file_id=view.id, owner_id="u1")
    assert ks.deleted == [("u1", view.id)]


@pytest.mark.asyncio
async def test_delete_still_succeeds_if_vector_cleanup_fails():
    # A vector-store outage shouldn't keep the user from deleting their
    # file. The metadata + bytes go away; the chunks become orphaned
    # but a janitor sweep can pick those up later.
    ks = _FakeKnowledgeService()
    ks.raise_on_delete = True
    svc, _ks = _service(ks)
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    # Doesn't raise.
    await svc.delete(file_id=view.id, owner_id="u1")
    with pytest.raises(FileNotFoundError):
        await svc.get(file_id=view.id, owner_id="u1")


# ---- captions --------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_caption_persists_and_get_returns_it():
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    # Fresh upload — no caption yet.
    assert (await svc.get(file_id=view.id, owner_id="u1")).caption is None
    await svc.set_caption(
        file_id=view.id, owner_id="u1", caption="a one-byte text file"
    )
    refreshed = await svc.get(file_id=view.id, owner_id="u1")
    assert refreshed.caption == "a one-byte text file"


@pytest.mark.asyncio
async def test_set_caption_silently_skips_cross_owner():
    # Foreign owner mustn't be able to overwrite another user's caption.
    svc, _ks = _service()
    view = await svc.upload(
        owner_id="u1", filename="x.txt", mime_type="text/plain", data=b"x"
    )
    await svc.set_caption(file_id=view.id, owner_id="u2", caption="injected")
    # Owner-A still sees None — the foreign write was rejected.
    assert (await svc.get(file_id=view.id, owner_id="u1")).caption is None


@pytest.mark.asyncio
async def test_set_caption_on_unknown_file_is_a_noop():
    # No file exists — set_caption logs + returns. No exception.
    svc, _ks = _service()
    await svc.set_caption(
        file_id="never-existed", owner_id="u1", caption="ghost"
    )
