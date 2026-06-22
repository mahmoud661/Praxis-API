"""HTTP adapter for the memory service — implements IMemoryClient.

Calls the memory service REST API so the ai-agents service can search and
store long-term memory without going through MCP. The client is a thin
httpx wrapper; all multi-tenancy is enforced by forwarding owner_id via
the X-User-Id header (same convention the gateway uses internally).
"""
from __future__ import annotations

import httpx

from ...domain.ports.i_memory_client import IMemoryClient, MemoryHit  # noqa: F401
from ...infrastructure.config.env import Env


class HttpMemoryClient:
    """Implements IMemoryClient against the memory-service REST API.

    DI token: ``"IMemoryClient"`` (resolved by annotation class name in
    the container — annotation is `IMemoryClient`, registered value is
    this class instance).
    """

    def __init__(self, env: Env) -> None:
        self._http = httpx.AsyncClient(
            base_url=env.memory_service_url.rstrip("/"),
            timeout=15.0,
        )

    async def search(
        self, *, owner_id: str, query: str, k: int = 10
    ) -> list[MemoryHit]:
        r = await self._http.get(
            "/knowledge/search",
            params={"q": query, "k": k},
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return [
            MemoryHit(
                excerpt=h["excerpt"],
                score=float(h.get("score", 0.0)),
                source=h.get("source", ""),
                entities=h.get("entities") or [],
            )
            for h in r.json().get("hits", [])
        ]

    async def store(
        self, *, owner_id: str, content: str, memory_type: str, thread_id: str | None = None
    ) -> str:
        body: dict = {"content": content, "memory_type": memory_type}
        if thread_id:
            body["thread_id"] = thread_id
        r = await self._http.post(
            "/knowledge/episodes",
            json=body,
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return r.json().get("episode_id", "")

    async def forget(self, *, owner_id: str, query: str) -> int:
        r = await self._http.post(
            "/knowledge/memories/forget",
            json={"query": query},
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return r.json().get("deleted", 0)

    async def provision_node(
        self, *, type: str, id: str, name: str, owner_id: str, summary: str = "", thread_id: str | None = None
    ) -> None:
        r = await self._http.post(
            "/provision",
            json={"type": type, "id": id, "name": name, "owner_id": owner_id, "summary": summary},
        )
        r.raise_for_status()
        if thread_id:
            await self.provision_link(
                from_id=thread_id, to_id=id, owner_id=owner_id, relationship="HAS_ATTACHMENT"
            )

    async def provision_link(
        self, *, from_id: str, to_id: str, owner_id: str, relationship: str
    ) -> None:
        r = await self._http.post(
            "/provision/link",
            json={"from_id": from_id, "to_id": to_id, "owner_id": owner_id, "relationship": relationship},
        )
        r.raise_for_status()

    async def clear(self, *, owner_id: str) -> None:
        r = await self._http.delete(
            "/knowledge/memories",
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._http.aclose()
