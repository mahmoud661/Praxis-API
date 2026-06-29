from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class Episode:
    owner_id: str
    content: str
    source: str                          # "conversation" | "document" | "web"
    id: str = ""
    thread_id: str = ""                  # originating conversation thread
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class MemoryEntity:
    name: str
    type: str                            # e.g. "Person", "Concept", "Event"
    summary: str = ""


@dataclass
class MemorySearchHit:
    episode_id: str
    excerpt: str
    score: float
    source: str
    entities: list[str] = field(default_factory=list)
    thread_name: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class GraphNode:
    id: str
    name: str
    type: str                            # entity type label
    summary: str = ""
    uuid: str = ""                       # Neo4j node uuid (file_id for Attachment/Image)
    deleted_at: str | None = None        # ISO-8601 when soft-deleted; None = active


@dataclass
class GraphEdge:
    source: str                          # GraphNode.id
    target: str                          # GraphNode.id
    label: str = ""                      # relationship type


@dataclass
class KnowledgeGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


class IMemoryStore(Protocol):
    async def init(self) -> None:
        """Build Neo4j indices and constraints. Idempotent."""
        pass

    async def add_episode(self, episode: Episode) -> str:
        """Persist episode; returns the assigned episode id."""
        pass

    async def search(
        self,
        *,
        owner_id: str,
        query: str,
        k: int = 10,
        source_filter: str | None = None,
    ) -> list[MemorySearchHit]:
        """Hybrid search. Empty query returns recent episodes.

        source_filter: if set, only return episodes whose source_description
        matches (e.g. "fact" or "conversation"). None means no filter.
        """
        pass

    async def list_entities(
        self, *, owner_id: str, limit: int = 50
    ) -> list[MemoryEntity]:
        pass

    async def get_graph(
        self, *, owner_id: str, limit: int = 100
    ) -> KnowledgeGraph:
        """Return entity nodes and their relationships for the knowledge graph."""
        pass

    async def provision_user(
        self, *, owner_id: str, email: str, registered_at: str
    ) -> None:
        """Create or merge the User entity node for a newly registered user."""
        pass

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
        """Create or merge an entity node of any type in the knowledge graph."""
        pass

    async def update_entity_name(
        self,
        *,
        owner_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        """Update the display name of an existing entity node."""
        pass

    async def link_entities(
        self,
        *,
        owner_id: str,
        from_entity_id: str,
        to_entity_id: str,
        relationship: str,
    ) -> None:
        """Create a directed relationship between two existing entity nodes."""
        pass

    async def soft_delete_entity(
        self,
        *,
        owner_id: str,
        entity_id: str,
        deleted_at: str,
    ) -> None:
        """Mark an entity node as soft-deleted (sets deleted_at timestamp)."""
        pass

    async def delete_episodes(self, *, owner_id: str, episode_ids: list[str]) -> int:
        """Delete specific episodes by id. Returns count deleted."""
        pass

    async def delete_episode(self, *, owner_id: str, episode_id: str) -> bool:
        """Delete a single episode by id. Returns True if found and deleted."""
        pass

    async def get_episode_status(self, *, owner_id: str, episode_id: str) -> bool:
        """Return True if the episode has been fully extracted (raw_content stamped)."""
        pass

    async def export_episodes(
        self, *, owner_id: str, tag: str | None = None
    ) -> list[dict]:
        """Export all episodes with metadata. Optional tag filter."""
        pass

    async def get_summary(self, *, owner_id: str) -> dict:
        """Return compact summary dict: entities, threads, facts."""
        pass

    async def get_entity_triples(
        self, *, owner_id: str, entity_name: str, k: int = 10
    ) -> list[dict]:
        """Return RELATES_TO triples for entities matching entity_name."""
        pass

    async def delete_by_owner(self, *, owner_id: str) -> None:
        pass

    async def close(self) -> None:
        pass
