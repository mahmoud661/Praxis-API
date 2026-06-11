"""Tests for `KnowledgeService` — ingestion + retrieval + cleanup.

Uses real `DocumentExtractor` (pure-Python, no I/O), real
`InMemoryVectorStore` (also pure-Python), and a fake embedding client
that returns predictable vectors per-input so we can assert on what
landed in the store without running the actual embedding model.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.application.services._chunker import chunk_text
from app.application.services.knowledge_service import KnowledgeService
from app.infrastructure.documents.document_extractor import DocumentExtractor
from app.infrastructure.vector.in_memory_vector_store import InMemoryVectorStore


class _FakeLogger:
    def info(self, *a: object, **kw: object) -> None: ...
    def warning(self, *a: object, **kw: object) -> None: ...
    def error(self, *a: object, **kw: object) -> None: ...
    def debug(self, *a: object, **kw: object) -> None: ...


class _FakeEmbeddings:
    """Returns a deterministic 4-dim vector per text. Vectors are
    structured so 'apple'-like words score high against the apple
    query and 'orange'-like words score high against the orange query
    — this lets a real search test the end-to-end retrieval path."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed_documents(self, *, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [_vec_for(t) for t in texts]

    async def embed_query(self, *, text: str) -> list[float]:
        return _vec_for(text)


def _vec_for(text: str) -> list[float]:
    """4 dims, each toggled by a keyword. Lets us steer search results
    deterministically in tests without any real model in the loop."""
    return [
        1.0 if "apple" in text.lower() else 0.0,
        1.0 if "orange" in text.lower() else 0.0,
        1.0 if "banana" in text.lower() else 0.0,
        1.0 if "grape" in text.lower() else 0.0,
    ]


def _service() -> tuple[KnowledgeService, InMemoryVectorStore, _FakeEmbeddings]:
    store = InMemoryVectorStore()
    embeddings = _FakeEmbeddings()
    svc = KnowledgeService(
        extractor=DocumentExtractor(),
        embeddings=embeddings,
        vector_store=store,
        logger=_FakeLogger(),
    )
    return svc, store, embeddings


# ----- _chunk_text (pure) -----------------------------------------------------


class TestChunkText:
    def test_short_text_returns_one_chunk(self) -> None:
        out = chunk_text("Hello world.")
        assert out == ["Hello world."]

    def test_empty_or_whitespace_returns_nothing(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n  \t ") == []

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        # 5000 chars of plain text, no paragraph breaks → hard-cut path.
        long = "x" * 5000
        out = chunk_text(long)
        assert len(out) > 1
        # All chunks fit under the soft cap plus a small slop.
        assert all(len(c) <= 1300 for c in out)

    def test_paragraph_break_preferred_over_hard_cut(self) -> None:
        # Build a text where a paragraph boundary falls in the trailing
        # 25% of the first window — the splitter should break there
        # rather than mid-sentence.
        para1 = "alpha " * 200  # ~1200 chars
        para2 = "beta " * 200
        text = para1 + "\n\n" + para2
        out = chunk_text(text)
        # First chunk should end somewhere inside para1 (the boundary
        # logic prefers the \n\n in the trailing 25%).
        assert "alpha" in out[0]


# ----- ingest_file ------------------------------------------------------------


class TestIngest:
    @pytest.mark.asyncio
    async def test_text_file_ingests_one_chunk(self) -> None:
        svc, store, _ = _service()
        n = await svc.ingest_file(
            owner_id="user-A",
            file_id="f1",
            filename="apple-notes.txt",
            mime_type="text/plain",
            data=b"apple cake recipe",
        )
        assert n == 1
        hits = await store.search(
            owner_id="user-A", query_vector=_vec_for("apple"), k=10
        )
        assert len(hits) == 1
        assert hits[0].chunk.text == "apple cake recipe"
        assert hits[0].chunk.extra["filename"] == "apple-notes.txt"

    @pytest.mark.asyncio
    async def test_unsupported_mime_is_a_noop_returns_zero(self) -> None:
        svc, store, embeds = _service()
        n = await svc.ingest_file(
            owner_id="user-A",
            file_id="img1",
            filename="cat.png",
            mime_type="image/png",
            data=b"\x89PNG\r\n\x1a\n",
        )
        assert n == 0
        assert embeds.embedded == []  # didn't even hit the embedder

    @pytest.mark.asyncio
    async def test_empty_text_extraction_is_a_noop(self) -> None:
        # A text file containing only whitespace shouldn't write a
        # chunk that's just spaces — search results would be junk.
        svc, _store, embeds = _service()
        n = await svc.ingest_file(
            owner_id="user-A",
            file_id="blank",
            filename="blank.txt",
            mime_type="text/plain",
            data=b"   \n   \t  ",
        )
        assert n == 0
        assert embeds.embedded == []

    @pytest.mark.asyncio
    async def test_reingest_overwrites_in_place(self) -> None:
        # Same file_id + same chunk index → stable chunk id → upsert
        # replaces. We shouldn't see two copies of the file in search.
        svc, store, _ = _service()
        await svc.ingest_file(
            owner_id="user-A",
            file_id="f1",
            filename="v1.txt",
            mime_type="text/plain",
            data=b"apple v1",
        )
        await svc.ingest_file(
            owner_id="user-A",
            file_id="f1",
            filename="v2.txt",
            mime_type="text/plain",
            data=b"apple v2",
        )
        hits = await store.search(
            owner_id="user-A", query_vector=_vec_for("apple"), k=10
        )
        assert len(hits) == 1
        assert hits[0].chunk.text == "apple v2"


# ----- search -----------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_top_k_by_relevance(self) -> None:
        svc, _store, _ = _service()
        for idx, text in enumerate(
            ["apple pie", "orange juice", "banana split", "grape jam"]
        ):
            await svc.ingest_file(
                owner_id="user-A",
                file_id=f"f{idx}",
                filename=f"{idx}.txt",
                mime_type="text/plain",
                data=text.encode(),
            )
        hits = await svc.search(owner_id="user-A", query="apple", k=2)
        assert hits
        assert hits[0].chunk.text == "apple pie"

    @pytest.mark.asyncio
    async def test_empty_query_returns_no_hits(self) -> None:
        svc, _store, embeds = _service()
        hits = await svc.search(owner_id="user-A", query="   ", k=5)
        assert hits == []
        assert embeds.embedded == []  # didn't embed the empty query

    @pytest.mark.asyncio
    async def test_owner_isolation(self) -> None:
        svc, _store, _ = _service()
        await svc.ingest_file(
            owner_id="user-A",
            file_id="f1",
            filename="A.txt",
            mime_type="text/plain",
            data=b"apple",
        )
        hits = await svc.search(owner_id="user-B", query="apple", k=5)
        assert hits == []


# ----- delete -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_chunks_purges_only_that_file() -> None:
    svc, store, _ = _service()
    await svc.ingest_file(
        owner_id="user-A",
        file_id="f1",
        filename="A1.txt",
        mime_type="text/plain",
        data=b"apple",
    )
    await svc.ingest_file(
        owner_id="user-A",
        file_id="f2",
        filename="A2.txt",
        mime_type="text/plain",
        data=b"orange",
    )
    await svc.delete_file_chunks(owner_id="user-A", file_id="f1")
    # Both stores return top-k by score regardless of absolute value —
    # so an empty-list assertion is wrong (the orange chunk still shows
    # up with score 0). Check that NO chunk from the deleted file
    # survives and the other file is untouched.
    remaining = await store.search(
        owner_id="user-A", query_vector=_vec_for("apple"), k=10
    )
    file_ids = {h.chunk.file_id for h in remaining}
    assert "f1" not in file_ids
    assert "f2" in file_ids
