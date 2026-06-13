"""
InMemoryVectorStore — dict-backed `IVectorStore` for unit tests.

Same interface as `QdrantVectorStore` but stores points in a plain
list and computes cosine similarity in Python. Not for production —
the search is O(n × dims) per query, fine for tests that hold a few
dozen chunks.

Identical owner-filtering semantics to the real backend so the
security test catches regressions equivalently. Same `id`-overwrite
behavior (stable chunk ids upsert in place).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ...domain.dtos.knowledge_dto import KnowledgeChunk, KnowledgeSearchHit


@dataclass
class _Point:
    chunk: KnowledgeChunk
    vector: list[float]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._points: dict[str, _Point] = {}

    async def ensure_ready(self) -> None:
        return None

    async def upsert(
        self,
        *,
        chunks: Sequence[KnowledgeChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunk/vector length mismatch: {len(chunks)} vs {len(vectors)}"
            )
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._points[chunk.id] = _Point(chunk=chunk, vector=list(vector))

    async def search(
        self,
        *,
        owner_id: str,
        query_vector: Sequence[float],
        k: int,
    ) -> list[KnowledgeSearchHit]:
        scored = [
            (p, _cosine(query_vector, p.vector))
            for p in self._points.values()
            if p.chunk.owner_id == owner_id
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [KnowledgeSearchHit(chunk=p.chunk, score=s) for p, s in scored[:k]]

    async def delete_by_file(self, *, owner_id: str, file_id: str) -> None:
        for key, point in list(self._points.items()):
            if point.chunk.owner_id == owner_id and point.chunk.file_id == file_id:
                del self._points[key]


# ---- module helpers ----------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain cosine — no numpy dep. Returns 0.0 on a zero vector
    rather than raising; matches Qdrant's "no match" behavior."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
