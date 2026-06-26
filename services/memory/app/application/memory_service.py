"""
MemoryService — thin orchestration layer between the HTTP/MCP surface
and the IMemoryStore port. All business rules live here; the store
adapter stays pure infrastructure.
"""
from __future__ import annotations

import uuid

from ..domain.ports.logger import Logger
from ..domain.ports.memory_store import (
    Episode,
    IMemoryStore,
    KnowledgeGraph,
    MemoryEntity,
    MemorySearchHit,
)


class MemoryService:
    def __init__(self, store: IMemoryStore, logger: Logger) -> None:
        self._store = store
        self._logger = logger

    _MAX_EPISODE_CHARS = 4000  # ~1000 tokens — keeps Graphiti extraction reliable

    async def add_episode(
        self, *, owner_id: str, content: str, source: str = "conversation", thread_id: str | None = None
    ) -> str:
        content = content.strip()
        if not content:
            return ""
        if len(content) > self._MAX_EPISODE_CHARS:
            content = content[: self._MAX_EPISODE_CHARS]
        episode = Episode(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            content=content,
            source=source,
            thread_id=thread_id or "",
        )
        episode_id = await self._store.add_episode(episode)
        self._logger.info("memory.episode_added", owner_id=owner_id, source=source)
        return episode_id

    async def search(
        self, *, owner_id: str, query: str, k: int = 10
    ) -> list[MemorySearchHit]:
        query = query.strip()
        if not query:
            return []
        hits = await self._store.search(owner_id=owner_id, query=query, k=k)
        self._logger.debug("memory.search", owner_id=owner_id, hits=len(hits))
        return hits

    async def list_memories(
        self, *, owner_id: str, k: int = 20
    ) -> list[MemorySearchHit]:
        """List recent episodes — calls store with empty query, bypassing the
        blank-query guard in `search` which is designed for explicit user searches."""
        return await self._store.search(owner_id=owner_id, query="", k=k)

    async def list_entities(
        self, *, owner_id: str, limit: int = 50
    ) -> list[MemoryEntity]:
        return await self._store.list_entities(owner_id=owner_id, limit=limit)

    async def get_graph(
        self, *, owner_id: str, limit: int = 100
    ) -> KnowledgeGraph:
        return await self._store.get_graph(owner_id=owner_id, limit=limit)

    async def provision_user(
        self, *, owner_id: str, email: str, registered_at: str
    ) -> None:
        """Create the User entity node when a new user registers.
        Called by the UserRegistered Kafka event handler."""
        await self._store.provision_user(
            owner_id=owner_id, email=email, registered_at=registered_at
        )
        self._logger.info("memory.user_provisioned", owner_id=owner_id)

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
        """Create or merge an entity node of any type.
        Called by EntityProvisioner implementations for each domain event."""
        await self._store.provision_entity(
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type=entity_type,
            name=name,
            summary=summary,
            created_at=created_at,
        )
        self._logger.info(
            "memory.entity_provisioned",
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type=entity_type,
        )

    async def update_entity_name(
        self,
        *,
        owner_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        """Update the display name of an existing entity node."""
        await self._store.update_entity_name(
            owner_id=owner_id,
            entity_id=entity_id,
            name=name,
        )
        self._logger.info(
            "memory.entity_name_updated",
            owner_id=owner_id,
            entity_id=entity_id,
        )

    async def link_entities(
        self,
        *,
        owner_id: str,
        from_entity_id: str,
        to_entity_id: str,
        relationship: str,
    ) -> None:
        """Link two existing entity nodes with a directed relationship."""
        await self._store.link_entities(
            owner_id=owner_id,
            from_entity_id=from_entity_id,
            to_entity_id=to_entity_id,
            relationship=relationship,
        )
        self._logger.info(
            "memory.entities_linked",
            owner_id=owner_id,
            from_entity_id=from_entity_id,
            to_entity_id=to_entity_id,
            relationship=relationship,
        )

    async def soft_delete_entity(
        self,
        *,
        owner_id: str,
        entity_id: str,
        deleted_at: str,
    ) -> None:
        """Mark an entity node as soft-deleted in the knowledge graph."""
        await self._store.soft_delete_entity(
            owner_id=owner_id,
            entity_id=entity_id,
            deleted_at=deleted_at,
        )
        self._logger.info(
            "memory.entity_soft_deleted",
            owner_id=owner_id,
            entity_id=entity_id,
        )

    async def forget(self, *, owner_id: str, query: str) -> int:
        """Search for episodes matching query and delete only high-confidence matches.

        A score threshold of 0.6 prevents loosely-related episodes from being
        deleted when the user asks to forget something specific.
        """
        _FORGET_THRESHOLD = 0.6
        hits = await self._store.search(owner_id=owner_id, query=query, k=5)
        if not hits:
            return 0
        relevant = [h for h in hits if h.score >= _FORGET_THRESHOLD and h.episode_id]
        if not relevant:
            return 0
        deleted = await self._store.delete_episodes(
            owner_id=owner_id,
            episode_ids=[h.episode_id for h in relevant],
        )
        self._logger.info("memory.forgotten", owner_id=owner_id, deleted=deleted)
        return deleted

    async def delete_memories(self, *, owner_id: str) -> None:
        await self._store.delete_by_owner(owner_id=owner_id)
        self._logger.info("memory.deleted", owner_id=owner_id)
