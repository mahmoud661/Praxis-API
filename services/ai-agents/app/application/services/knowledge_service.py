"""
KnowledgeService — orchestrates the ingestion + retrieval pipeline
behind the `kb_search` agent tool.

Pipeline at a glance:

    ingest:    bytes  -> extract_text -> chunk -> embed -> upsert
    search:    query  -> embed -> vector store lookup (owner-scoped)
    cleanup:   file_id -> drop every chunk in the vector store

All operations are user-scoped. Search results are filtered by
`owner_id` at the vector-store layer, not by callers, so there's no
path where a missed filter leaks one user's chunks into another's
results.

Chunking lives in `_chunker.py` so this file stays readable as pure
orchestration. Underscore-prefixed module name keeps it out of the DI
auto-discovery globber.

Auto-bound to the DI token `"IKnowledgeService"`.
"""

from __future__ import annotations

from ...domain.dtos.knowledge_dto import KnowledgeChunk, KnowledgeSearchHit
from ...domain.ports.document_extractor import IDocumentExtractor
from ...domain.ports.embedding_client import IEmbeddingClient
from ...domain.ports.logger import Logger
from ...domain.ports.vector_store import IVectorStore
from ._chunker import chunk_text
from ._errors import UnsupportedMimeTypeError


class KnowledgeService:
    """Auto-bound to the DI token `"IKnowledgeService"`."""

    def __init__(
        self,
        extractor: IDocumentExtractor,
        embeddings: IEmbeddingClient,
        vector_store: IVectorStore,
        logger: Logger,
    ) -> None:
        self._extractor = extractor
        self._embeddings = embeddings
        self._vector = vector_store
        self._logger = logger

    async def ingest_file(
        self,
        *,
        owner_id: str,
        file_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> int:
        """Extract text → chunk → embed → upsert. Returns the number of
        chunks written. Zero is a normal, non-error result for files
        the extractor doesn't index (images, audio, empty PDFs)."""
        text = self._extract_or_skip(file_id=file_id, mime_type=mime_type, data=data)
        if not text:
            return 0

        chunks = _build_chunks(
            owner_id=owner_id,
            file_id=file_id,
            filename=filename,
            mime_type=mime_type,
            pieces=chunk_text(text),
        )
        vectors = await self._embeddings.embed_documents(
            texts=[c.text for c in chunks]
        )
        await self._vector.upsert(chunks=chunks, vectors=vectors)
        self._logger.info(
            "kb.ingest_done",
            file_id=file_id,
            owner_id=owner_id,
            chunks=len(chunks),
        )
        return len(chunks)

    async def search(
        self, *, owner_id: str, query: str, k: int = 5
    ) -> list[KnowledgeSearchHit]:
        """Top-k chunks owned by `owner_id` matching `query`. Empty
        query returns `[]` without hitting the embedder."""
        cleaned = query.strip()
        if not cleaned:
            return []
        vector = await self._embeddings.embed_query(text=cleaned)
        return await self._vector.search(
            owner_id=owner_id, query_vector=vector, k=k
        )

    async def delete_file_chunks(self, *, owner_id: str, file_id: str) -> None:
        """Drop every chunk belonging to one file. Called by
        `FilesService.delete` so the vector store can't outlive the
        source. Idempotent."""
        await self._vector.delete_by_file(owner_id=owner_id, file_id=file_id)

    # ---- internals --------------------------------------------------------

    def _extract_or_skip(
        self, *, file_id: str, mime_type: str, data: bytes
    ) -> str:
        """Try to pull plain text. Returns `""` for any soft-skip
        reason — unsupported MIME, empty extraction — and logs at
        debug. The caller treats `""` as "nothing to ingest" and
        returns 0 without raising."""
        try:
            text = self._extractor.extract_text(data=data, mime_type=mime_type)
        except UnsupportedMimeTypeError:
            self._logger.debug(
                "kb.ingest_skipped_unsupported",
                file_id=file_id,
                mime_type=mime_type,
            )
            return ""
        text = text.strip()
        if not text:
            self._logger.debug(
                "kb.ingest_skipped_empty",
                file_id=file_id,
                mime_type=mime_type,
            )
        return text


# ---- module helpers -----------------------------------------------------------


def _build_chunks(
    *,
    owner_id: str,
    file_id: str,
    filename: str,
    mime_type: str,
    pieces: list[str],
) -> list[KnowledgeChunk]:
    """Wrap each chunk-text piece in a `KnowledgeChunk` with the
    metadata the vector store + the citation alias path need."""
    return [
        KnowledgeChunk(
            id=f"{file_id}:{idx}",
            owner_id=owner_id,
            file_id=file_id,
            chunk_index=idx,
            text=piece,
            extra={"filename": filename, "mime_type": mime_type},
        )
        for idx, piece in enumerate(pieces)
    ]
