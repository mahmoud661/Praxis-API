"""
Port for turning text into embedding vectors via the model proxy.

One method, two shapes:

  - `embed_documents(texts)` — batch path for ingestion. Returns one
    vector per input text, in the same order. Used by
    `KnowledgeService` when chunking a freshly uploaded file.

  - `embed_query(text)` — single-vector path for search. Returns one
    vector. Some providers train query vs document encoders to behave
    slightly differently (asymmetric retrieval); the port keeps the
    two calls separate so we can wire that in later without breaking
    callers.

All vectors come back as plain `list[float]` so we don't bleed numpy
or provider-specific tensor types into the domain.

Implementations live in `app/infrastructure/llm/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class IEmbeddingClient(Protocol):
    async def embed_documents(self, *, texts: Sequence[str]) -> list[list[float]]:
        """Embed N texts → N vectors, same order. Empty `texts` returns
        an empty list without calling the upstream API."""

    async def embed_query(self, *, text: str) -> list[float]:
        """Embed one query string → one vector."""

    async def aclose(self) -> None:
        """Release any underlying connection pool / file descriptors.
        Called from the FastAPI lifespan shutdown — implementations
        without resources to release should be a no-op."""
