"""
Port for a per-user vector store of knowledge-base chunks.

Backend-agnostic — concrete implementations (Qdrant today, possibly
pgvector or in-memory tomorrow) live in `app/infrastructure/vector/`.

Two operations the agent + ingestion pipeline need:

  - `upsert(chunks, vectors)` — write a batch of chunks with their
    pre-computed embedding vectors. Chunk ids are stable (`{file_id}:
    {chunk_index}`) so re-ingestion replaces in place rather than
    appending duplicates.

  - `search(owner_id, query_vector, k)` — return the top-k closest
    chunks owned by `owner_id`. Owner filtering is enforced at the
    store level, not by the caller, so there's no path where a missed
    filter leaks one user's chunks into another's results.

Backends that need an explicit setup step (Qdrant collection creation)
expose an `ensure_ready()` so callers can boot-time-init without
coupling to backend specifics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..dtos.knowledge_dto import KnowledgeChunk, KnowledgeSearchHit


class IVectorStore(Protocol):
    async def ensure_ready(self) -> None:
        """Idempotent bring-up — creates the underlying collection /
        index if it doesn't exist. Called once at service boot."""

    async def upsert(
        self,
        *,
        chunks: Sequence[KnowledgeChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """Write `chunks[i]` with embedding `vectors[i]`. `len(chunks)`
        must equal `len(vectors)`; backends raise `ValueError` on
        mismatch. Stable chunk ids mean a repeat call with the same
        ids overwrites in place."""

    async def search(
        self,
        *,
        owner_id: str,
        query_vector: Sequence[float],
        k: int,
    ) -> list[KnowledgeSearchHit]:
        """Top-k nearest chunks owned by `owner_id`. Empty list if the
        owner has no chunks yet. Hits sorted by score, highest first."""

    async def delete_by_file(self, *, owner_id: str, file_id: str) -> None:
        """Drop every chunk belonging to one file. Called when a file
        is deleted via `FilesService.delete` so the vector store
        doesn't outlive the source."""
