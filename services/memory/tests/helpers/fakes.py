from __future__ import annotations

from typing import Any

from app.domain.ports.memory_store import (
    Episode,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    MemoryEntity,
    MemorySearchHit,
)


class FakeMemoryStore:
    def __init__(self) -> None:
        self._episodes: list[dict] = []
        self._injected_hits: list[MemorySearchHit] = []
        self._injected_entities: list[MemoryEntity] = []
        self._injected_graph: KnowledgeGraph = KnowledgeGraph()
        self._provisioned_users: list[dict] = []

    def inject_hit(self, hit: MemorySearchHit) -> None:
        self._injected_hits.append(hit)

    def inject_entity(self, entity: MemoryEntity) -> None:
        self._injected_entities.append(entity)

    def inject_graph(self, graph: KnowledgeGraph) -> None:
        self._injected_graph = graph

    async def init(self) -> None:
        pass

    async def add_episode(self, episode: Episode) -> str:
        self._episodes.append(
            {"id": episode.id, "owner_id": episode.owner_id, "content": episode.content,
             "source": episode.source}
        )
        return episode.id

    async def search(
        self, *, owner_id: str, query: str, k: int = 10
    ) -> list[MemorySearchHit]:
        return list(self._injected_hits[:k])

    async def list_entities(
        self, *, owner_id: str, limit: int = 50
    ) -> list[MemoryEntity]:
        return list(self._injected_entities[:limit])

    async def get_graph(
        self, *, owner_id: str, limit: int = 100
    ) -> KnowledgeGraph:
        return self._injected_graph

    async def provision_user(
        self, *, owner_id: str, email: str, registered_at: str
    ) -> None:
        self._provisioned_users.append(
            {"owner_id": owner_id, "email": email, "registered_at": registered_at}
        )

    async def provision_entity(
        self,
        *,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        name: str,
        summary: str = "",
        created_at: str = "",
    ) -> None:
        self._provisioned_users.append(
            {
                "owner_id": owner_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "name": name,
                "summary": summary,
                "created_at": created_at,
            }
        )

    async def update_entity_name(
        self,
        *,
        owner_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        pass

    async def link_entities(
        self,
        *,
        owner_id: str,
        from_entity_id: str,
        to_entity_id: str,
        relationship: str,
    ) -> None:
        pass

    async def delete_by_owner(self, *, owner_id: str) -> None:
        self._episodes = [e for e in self._episodes if e["owner_id"] != owner_id]

    async def close(self) -> None:
        pass


class SilentLogger:
    def debug(self, msg: str, **ctx: Any) -> None: pass
    def info(self, msg: str, **ctx: Any) -> None: pass
    def warning(self, msg: str, **ctx: Any) -> None: pass
    def error(self, msg: str, **ctx: Any) -> None: pass
