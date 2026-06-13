"""
EmbeddingClient — `IEmbeddingClient` over the LiteLLM proxy's
OpenAI-compatible `/embeddings` endpoint.

The proxy normalises whatever upstream embedding model is configured
(`text-embedding-3-small`, voyage, cohere, ...) into the OpenAI shape:

    POST /embeddings   { "model": "...", "input": ["..."], ... }
    →    { "data": [ { "embedding": [floats] }, ... ] }

Same auth + base URL as the chat client (`Env.litellm_proxy_*`). One
shared `httpx.AsyncClient` per instance — Connect/keep-alive matters
for ingestion runs that embed dozens of chunks back to back.

Auto-bound to the DI token `"IEmbeddingClient"`.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from ...domain.ports.logger import Logger
from ..config.env import Env


class EmbeddingClient:
    """Auto-bound to the DI token `"IEmbeddingClient"`."""

    def __init__(self, env: Env, logger: Logger) -> None:
        self._model = env.embedding_model
        self._logger = logger
        # Keep-alive matters for batch ingestion. The proxy may be on
        # the same VPC but we still pay TLS setup per request without
        # connection reuse.
        self._client = httpx.AsyncClient(
            base_url=env.litellm_proxy_api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {env.litellm_proxy_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def embed_documents(self, *, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(list(texts))

    async def embed_query(self, *, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        response = await self._client.post(
            "/embeddings",
            json={"model": self._model, "input": inputs},
        )
        response.raise_for_status()
        body = response.json()
        # The proxy returns data in input order — documented OpenAI
        # behavior, mirrored by LiteLLM. We re-sort defensively in
        # case a future provider doesn't honor that.
        data = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
        return [list(item["embedding"]) for item in data]

    async def aclose(self) -> None:
        """Service shutdown hook — `AsyncClient` needs explicit close
        to flush its connection pool."""
        await self._client.aclose()
