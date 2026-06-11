"""
QdrantVectorStore — `IVectorStore` backed by a single Qdrant collection
with per-user isolation enforced via a payload filter on `owner_id`.

One collection, many users. Qdrant performs much better with a single
filtered collection than with thousands of small per-user ones (HNSW
index cost is per-collection). The filter on every `search()` call is
the line that ensures one user never sees another's chunks; missing
the filter is a security incident — there's a test for it.

Distance metric: cosine — matches what most embedding models train
for, and matches the LiteLLM `text-embedding-3-*` family that's the
default in our `Env`. Switching distance requires re-provisioning the
collection.

Chunk ids (`{file_id}:{chunk_index}`) are passed straight to Qdrant
as the point id via `uuid5(namespace, chunk_id)`. We can't use the
raw string directly — Qdrant requires either an int or a UUID — but
the namespace-uuid form is deterministic, so re-ingesting the same
file produces the same point ids and upserts cleanly.

Auto-bound to the DI token `"IVectorStore"`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from ...domain.dtos.knowledge_dto import KnowledgeChunk, KnowledgeSearchHit
from ...domain.ports.logger import Logger
from ..config.env import Env


# Deterministic namespace so the same chunk id always maps to the same
# point UUID. Hardcoded literal — must NOT change across deploys, or
# all existing points become unreachable. Any UUID4 works; this one
# was generated once and pinned.
_POINT_ID_NAMESPACE = uuid.UUID("8b1b7c2f-3a4b-4d6e-9f0a-1c2d3e4f5a6b")


class QdrantVectorStore:
    """Auto-bound to the DI token `"IVectorStore"`."""

    def __init__(self, env: Env, logger: Logger) -> None:
        self._collection = env.qdrant_collection
        self._vector_size = env.embedding_vector_size
        self._logger = logger
        self._client = AsyncQdrantClient(
            url=env.qdrant_url,
            api_key=env.qdrant_api_key,
        )

    async def ensure_ready(self) -> None:
        # Create the collection if missing. `collection_exists` is the
        # cheapest probe; recreating an existing collection would wipe
        # data, so we go via "exists → create only if not".
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(
                size=self._vector_size,
                distance=Distance.COSINE,
            ),
        )
        # Payload index on `owner_id` — without it, the filter on every
        # search degrades to a full scan once the collection grows.
        # Idempotent if it already exists.
        try:
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name="owner_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except UnexpectedResponse:
            # Race with another worker creating it first — fine.
            pass
        self._logger.info(
            "qdrant.collection_provisioned",
            collection=self._collection,
            vector_size=self._vector_size,
        )

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
        if not chunks:
            return
        points = [
            PointStruct(
                id=_point_id_for(chunk.id),
                vector=list(vector),
                payload={
                    "chunk_id": chunk.id,
                    "owner_id": chunk.owner_id,
                    "file_id": chunk.file_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "extra": chunk.extra,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        await self._client.upsert(
            collection_name=self._collection,
            points=points,
        )

    async def search(
        self,
        *,
        owner_id: str,
        query_vector: Sequence[float],
        k: int,
    ) -> list[KnowledgeSearchHit]:
        # Owner filter is the security boundary. If this line is wrong,
        # one user sees another user's chunks. The test
        # `test_search_filters_by_owner_id` verifies a foreign owner
        # gets zero hits even when their chunks are the closest matches.
        result = await self._client.query_points(
            collection_name=self._collection,
            query=list(query_vector),
            limit=k,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="owner_id",
                        match=MatchValue(value=owner_id),
                    )
                ]
            ),
            with_payload=True,
        )
        return [_hit_from_point(p) for p in result.points]

    async def delete_by_file(self, *, owner_id: str, file_id: str) -> None:
        await self._client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="owner_id",
                        match=MatchValue(value=owner_id),
                    ),
                    FieldCondition(
                        key="file_id",
                        match=MatchValue(value=file_id),
                    ),
                ]
            ),
        )


# ---- module helpers ----------------------------------------------------------


def _point_id_for(chunk_id: str) -> str:
    """Map our `file_id:chunk_index` string to a Qdrant-acceptable
    UUID. Deterministic via uuid5 over a pinned namespace."""
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, chunk_id))


def _hit_from_point(point) -> KnowledgeSearchHit:
    payload = point.payload or {}
    chunk = KnowledgeChunk(
        id=str(payload.get("chunk_id", "")),
        owner_id=str(payload.get("owner_id", "")),
        file_id=str(payload.get("file_id", "")),
        chunk_index=int(payload.get("chunk_index", 0) or 0),
        text=str(payload.get("text", "")),
        extra=dict(payload.get("extra") or {}),
    )
    return KnowledgeSearchHit(chunk=chunk, score=float(point.score or 0.0))
