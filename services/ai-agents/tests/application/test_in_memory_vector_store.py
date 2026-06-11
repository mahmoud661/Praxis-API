"""Tests for `InMemoryVectorStore` — the contract every `IVectorStore`
implementation must honor. The Qdrant adapter is integration-tested
elsewhere (requires a running Qdrant); the in-memory store is what
exercises the upsert/search/filter semantics here.
"""

from __future__ import annotations

import pytest

from app.domain.dtos.knowledge_dto import KnowledgeChunk
from app.infrastructure.vector.in_memory_vector_store import InMemoryVectorStore


def _chunk(
    *,
    id: str = "f1:0",
    owner_id: str = "user-A",
    file_id: str = "f1",
    chunk_index: int = 0,
    text: str = "hello",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=id,
        owner_id=owner_id,
        file_id=file_id,
        chunk_index=chunk_index,
        text=text,
    )


class TestUpsert:
    @pytest.mark.asyncio
    async def test_empty_inputs_are_a_noop(self) -> None:
        store = InMemoryVectorStore()
        await store.upsert(chunks=[], vectors=[])
        # And a subsequent search returns nothing.
        hits = await store.search(owner_id="user-A", query_vector=[1.0], k=5)
        assert hits == []

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self) -> None:
        store = InMemoryVectorStore()
        with pytest.raises(ValueError):
            await store.upsert(chunks=[_chunk()], vectors=[])

    @pytest.mark.asyncio
    async def test_same_id_overwrites_in_place(self) -> None:
        # Critical for re-ingestion — same `{file_id}:{chunk_index}`
        # must replace, not append, or we'd bloat the store.
        store = InMemoryVectorStore()
        await store.upsert(chunks=[_chunk(text="v1")], vectors=[[1.0, 0.0]])
        await store.upsert(chunks=[_chunk(text="v2")], vectors=[[1.0, 0.0]])
        hits = await store.search(
            owner_id="user-A", query_vector=[1.0, 0.0], k=10
        )
        assert len(hits) == 1
        assert hits[0].chunk.text == "v2"


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_closest_first(self) -> None:
        store = InMemoryVectorStore()
        await store.upsert(
            chunks=[
                _chunk(id="f1:0", text="far"),
                _chunk(id="f1:1", chunk_index=1, text="near"),
            ],
            vectors=[[0.0, 1.0], [1.0, 0.0]],
        )
        hits = await store.search(
            owner_id="user-A", query_vector=[1.0, 0.0], k=2
        )
        assert [h.chunk.text for h in hits] == ["near", "far"]
        assert hits[0].score > hits[1].score

    @pytest.mark.asyncio
    async def test_filters_by_owner_id(self) -> None:
        # The security boundary — a foreign owner must never see
        # another user's chunks even when they're the closest match.
        store = InMemoryVectorStore()
        await store.upsert(
            chunks=[
                _chunk(id="A:0", owner_id="user-A", text="A's secret"),
            ],
            vectors=[[1.0, 0.0]],
        )
        hits = await store.search(
            owner_id="user-B", query_vector=[1.0, 0.0], k=10
        )
        assert hits == []

    @pytest.mark.asyncio
    async def test_respects_k(self) -> None:
        store = InMemoryVectorStore()
        await store.upsert(
            chunks=[
                _chunk(id=f"f:{i}", chunk_index=i, text=f"chunk-{i}")
                for i in range(5)
            ],
            vectors=[[float(i + 1), 0.0] for i in range(5)],
        )
        hits = await store.search(
            owner_id="user-A", query_vector=[1.0, 0.0], k=3
        )
        assert len(hits) == 3


class TestDeleteByFile:
    @pytest.mark.asyncio
    async def test_drops_only_matching_file_for_owner(self) -> None:
        store = InMemoryVectorStore()
        await store.upsert(
            chunks=[
                _chunk(id="A:f1:0", owner_id="user-A", file_id="f1"),
                _chunk(id="A:f2:0", owner_id="user-A", file_id="f2"),
                _chunk(id="B:f1:0", owner_id="user-B", file_id="f1"),
            ],
            vectors=[[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]],
        )
        await store.delete_by_file(owner_id="user-A", file_id="f1")
        a_hits = await store.search(
            owner_id="user-A", query_vector=[1.0, 0.0], k=10
        )
        b_hits = await store.search(
            owner_id="user-B", query_vector=[1.0, 0.0], k=10
        )
        # User A's f1 gone, f2 stays; user B's f1 untouched.
        assert [h.chunk.file_id for h in a_hits] == ["f2"]
        assert [h.chunk.file_id for h in b_hits] == ["f1"]
