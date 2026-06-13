"""DTOs for the knowledge-base / RAG pipeline.

A `KnowledgeChunk` is one stored fragment of a user's document — text +
the metadata we need to (a) re-embed it correctly, (b) deduplicate
chunks from the same source on re-upload, and (c) cite back to the
file the chunk came from when the agent surfaces a search hit.

A `KnowledgeSearchHit` is what `IVectorStore.search()` returns: the
chunk plus the similarity score the backend assigned. Score semantics
are backend-specific (cosine for Qdrant by default — closer to 1.0 is
more similar) but kept as a plain float here so callers don't have to
care which backend they're talking to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One stored fragment of a user's document."""

    id: str
    """Stable identifier for this chunk. Composed as
    `{file_id}:{chunk_index}` so re-uploading the same file produces
    the same chunk ids and the upsert overwrites cleanly instead of
    bloating the collection with duplicates."""

    owner_id: str
    """User who owns the source file. Every search filters on this so
    one user can never see another user's chunks."""

    file_id: str
    """The `FilesService` file this chunk came from. Used to cite back
    to the source when the agent surfaces a search hit."""

    chunk_index: int
    """0-based position of this chunk within the source file. Useful
    for ordering hits from the same document when several land in the
    same search."""

    text: str
    """The chunk's raw text — what the model reads."""

    extra: dict[str, str] = field(default_factory=dict)
    """Free-form metadata (filename, mime, section title, ...). The
    vector store carries this through but doesn't interpret it."""


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    """One match from a vector-store search."""

    chunk: KnowledgeChunk
    score: float
    """Backend-specific similarity score. Higher is better."""
