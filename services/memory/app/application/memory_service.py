"""
MemoryService — thin orchestration layer between the HTTP/MCP surface
and the IMemoryStore port. All business rules live here; the store
adapter stays pure infrastructure.
"""
from __future__ import annotations

import uuid
from time import monotonic

from ..domain.ports.logger import Logger
from ..domain.ports.memory_store import (
    Episode,
    IMemoryStore,
    KnowledgeGraph,
    MemoryEntity,
    MemorySearchHit,
)
from ..domain.settings import (
    CONTEXT_CACHE_TTL,
    DEFAULT_LIST_K,
    FORGET_SCORE_THRESHOLD,
    MAX_EPISODE_CHARS,
)

# In-process TTL cache keyed by owner_id → (context_str, expiry_monotonic).
# Single-process service: no Redis needed.
_CONTEXT_CACHE: dict[str, tuple[str, float]] = {}


class MemoryService:
    def __init__(self, store: IMemoryStore, logger: Logger) -> None:
        self._store = store
        self._logger = logger

    async def add_episode(
        self,
        *,
        owner_id: str,
        content: str,
        source: str = "conversation",
        thread_id: str | None = None,
        episode_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        content = content.strip()
        if not content:
            return ""
        if len(content) > MAX_EPISODE_CHARS:
            content = content[:MAX_EPISODE_CHARS]
        episode = Episode(
            id=episode_id or str(uuid.uuid4()),
            owner_id=owner_id,
            content=content,
            source=source,
            thread_id=thread_id or "",
            tags=tags or [],
        )
        try:
            episode_id = await self._store.add_episode(episode)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "memory.episode_extraction_failed",
                owner_id=owner_id,
                source=source,
                error=str(exc),
            )
            raise
        # Invalidate context cache so the next get_context_summary reflects
        # the new episode (the cache would otherwise serve stale context for
        # up to CONTEXT_CACHE_TTL seconds after a new memory is stored).
        _CONTEXT_CACHE.pop(owner_id, None)
        self._logger.info("memory.episode_added", owner_id=owner_id, source=source)
        return episode_id

    async def search(
        self,
        *,
        owner_id: str,
        query: str,
        k: int = 10,
        source_filter: str | None = None,
    ) -> list[MemorySearchHit]:
        query = query.strip()
        if not query:
            return []
        hits = await self._store.search(
            owner_id=owner_id, query=query, k=k, source_filter=source_filter
        )
        self._logger.debug("memory.search", owner_id=owner_id, hits=len(hits))
        return hits

    async def list_memories(
        self, *, owner_id: str, k: int = DEFAULT_LIST_K
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

    async def get_context_summary(self, *, owner_id: str) -> str:
        """Return a concise, agent-readable context string about this user.

        Pulls top extracted entities, recent threads, and stored facts from
        Neo4j and formats them into a brief paragraph the agent injects as a
        SystemMessage at the start of each new thread. Result is cached for
        CONTEXT_CACHE_TTL seconds and invalidated whenever a new episode is stored.
        """
        cached = _CONTEXT_CACHE.get(owner_id)
        if cached and monotonic() < cached[1]:
            return cached[0]
        data = await self._store.get_summary(owner_id=owner_id)
        entities: list[dict] = data.get("entities", [])
        threads: list[str] = data.get("threads", [])
        facts: list[str] = data.get("facts", [])

        if not entities and not facts:
            return ""

        lines: list[str] = ["[User context from long-term memory]"]

        if entities:
            entity_names = ", ".join(e["name"] for e in entities[:6])
            lines.append(f"Known entities: {entity_names}")

        if facts:
            lines.append("Known facts/preferences:")
            for f in facts[:5]:
                lines.append(f"  • {f.strip()}")

        if threads:
            lines.append(f"Recent conversations: {', '.join(threads[:3])}")

        lines.append(
            "Use this context to personalise your responses. "
            "Call memory_search for deeper recall on any topic."
        )
        context = "\n".join(lines)
        _CONTEXT_CACHE[owner_id] = (context, monotonic() + CONTEXT_CACHE_TTL)
        return context

    async def get_episode_status(self, *, owner_id: str, episode_id: str) -> bool:
        """Return True if the episode has been fully extracted (raw_content stamped)."""
        return await self._store.get_episode_status(
            owner_id=owner_id, episode_id=episode_id
        )

    async def export_memories(
        self, *, owner_id: str, tag: str | None = None
    ) -> list[dict]:
        """Export all episodes with metadata. Optional tag filter."""
        return await self._store.export_episodes(owner_id=owner_id, tag=tag)

    async def delete_episode(self, *, owner_id: str, episode_id: str) -> bool:
        """Delete a specific episode by id. Returns True if found and deleted."""
        deleted = await self._store.delete_episode(
            owner_id=owner_id, episode_id=episode_id
        )
        if deleted:
            _CONTEXT_CACHE.pop(owner_id, None)
            self._logger.info(
                "memory.episode_deleted", owner_id=owner_id, episode_id=episode_id
            )
        return deleted

    async def get_entity_triples(
        self, *, owner_id: str, entity_name: str, k: int = 10
    ) -> list[dict]:
        """Return relationship triples for entities matching entity_name."""
        return await self._store.get_entity_triples(
            owner_id=owner_id, entity_name=entity_name, k=k
        )

    async def forget(self, *, owner_id: str, query: str) -> int:
        """Search for episodes matching query and delete only high-confidence matches."""
        hits = await self._store.search(owner_id=owner_id, query=query, k=5)
        if not hits:
            return 0
        relevant = [h for h in hits if h.score >= FORGET_SCORE_THRESHOLD and h.episode_id]
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
