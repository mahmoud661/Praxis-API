"""HTTP adapter for the memory service — implements IMemoryClient.

Calls the memory service REST API so the ai-agents service can search and
store long-term memory without going through MCP. The client is a thin
httpx wrapper; all multi-tenancy is enforced by forwarding owner_id via
the X-User-Id header (same convention the gateway uses internally).
"""
from __future__ import annotations

import httpx

from ...domain.ports.i_memory_client import GraphTriple, IMemoryClient, MemoryHit  # noqa: F401
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
            # store() returns immediately (fire-and-forget 201) so the 15 s
            # read timeout covers all endpoints including episode ingestion.
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )

    async def search(
        self, *, owner_id: str, query: str, k: int = 10, memory_type: str = "all"
    ) -> list[MemoryHit]:
        # Empty query → list endpoint (/search enforces min_length=1 on q).
        if not query.strip():
            r = await self._http.get(
                "/knowledge/memories",
                params={"k": k},
                headers={"x-user-id": owner_id},
            )
            r.raise_for_status()
            return [
                MemoryHit(
                    excerpt=h["excerpt"],
                    score=float(h.get("score", 1.0)),
                    source=h.get("source", ""),
                    entities=h.get("entities") or [],
                    thread_name=h.get("thread_name") or "",
                    tags=h.get("tags") or [],
                )
                for h in r.json()
            ]
        params: dict = {"q": query, "k": k}
        if memory_type != "all":
            params["source"] = "fact" if memory_type == "semantic" else "conversation"
        r = await self._http.get(
            "/knowledge/search",
            params=params,
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return [
            MemoryHit(
                excerpt=h["excerpt"],
                score=float(h.get("score", 0.0)),
                source=h.get("source", ""),
                entities=h.get("entities") or [],
                thread_name=h.get("thread_name") or "",
                tags=h.get("tags") or [],
            )
            for h in r.json().get("hits", [])
        ]

    async def store(
        self,
        *,
        owner_id: str,
        content: str,
        memory_type: str,
        thread_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        body: dict = {"content": content, "memory_type": memory_type}
        if thread_id:
            body["thread_id"] = thread_id
        if tags:
            body["tags"] = tags
        # Endpoint returns immediately (202-style) — extraction runs in the
        # memory service background, so the default 5s timeout is enough.
        r = await self._http.post(
            "/knowledge/episodes",
            json=body,
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return r.json().get("episode_id", "")

    async def delete_episode(self, *, owner_id: str, episode_id: str) -> bool:
        r = await self._http.delete(
            f"/knowledge/episodes/{episode_id}",
            headers={"x-user-id": owner_id},
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    async def get_episode_status(self, *, owner_id: str, episode_id: str) -> bool:
        r = await self._http.get(
            f"/knowledge/episodes/{episode_id}/status",
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return bool(r.json().get("extracted", False))

    async def forget(self, *, owner_id: str, query: str) -> int:
        r = await self._http.post(
            "/knowledge/memories/forget",
            json={"query": query},
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return r.json().get("deleted", 0)

    async def provision_node(
        self,
        *,
        node_type: str,
        node_id: str,
        name: str,
        owner_id: str,
        summary: str = "",
        thread_id: str | None = None,
    ) -> None:
        r = await self._http.post(
            "/provision",
            json={"type": node_type, "id": node_id, "name": name, "owner_id": owner_id, "summary": summary},
        )
        r.raise_for_status()
        if thread_id:
            await self.provision_link(
                from_id=thread_id, to_id=node_id, owner_id=owner_id, relationship="HAS_ATTACHMENT"
            )

    async def provision_link(
        self, *, from_id: str, to_id: str, owner_id: str, relationship: str
    ) -> None:
        r = await self._http.post(
            "/provision/link",
            json={"from_id": from_id, "to_id": to_id, "owner_id": owner_id, "relationship": relationship},
        )
        r.raise_for_status()

    async def get_context(self, *, owner_id: str) -> str:
        r = await self._http.get(
            "/knowledge/summary",
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return r.json().get("context", "")

    async def graph_search(
        self, *, owner_id: str, entity: str, k: int = 10
    ) -> list[GraphTriple]:
        r = await self._http.get(
            "/knowledge/graph/context",
            params={"entity": entity, "k": k},
            headers={"x-user-id": owner_id},
        )
        r.raise_for_status()
        return [
            GraphTriple(
                subject=t["subject"],
                predicate=t["predicate"],
                object=t["object"],
                fact=t["fact"],
            )
            for t in r.json().get("triples", [])
        ]

    async def aclose(self) -> None:
        await self._http.aclose()
