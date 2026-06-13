"""DI token `"IKnowledgeService"` (impl class `KnowledgeService`)."""

from __future__ import annotations

from typing import Protocol

from ..dtos.knowledge_dto import KnowledgeSearchHit


class IKnowledgeService(Protocol):
    async def ingest_file(
        self,
        *,
        owner_id: str,
        file_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> int:
        """Chunk + embed + upsert. Returns chunks written. Files the
        extractor can't index (images, audio, empty PDFs) return 0
        without raising."""

    async def search(
        self, *, owner_id: str, query: str, k: int = 5
    ) -> list[KnowledgeSearchHit]:
        """Top-k chunks owned by `owner_id` matching `query`. Empty
        query returns []."""

    async def delete_file_chunks(self, *, owner_id: str, file_id: str) -> None:
        """Drop every chunk belonging to one file. Idempotent."""
