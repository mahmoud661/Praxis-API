"""DI token `"IMemoryClient"` — port for the memory service."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """A single search result from the memory service."""

    excerpt: str
    score: float
    source: str
    entities: list[str] = field(default_factory=list)
    thread_name: str = ""


@dataclass(frozen=True, slots=True)
class GraphTriple:
    """A subject → predicate → object relationship triple from the graph."""

    subject: str
    predicate: str
    object: str
    fact: str


class IMemoryClient(Protocol):
    """Port for the memory service HTTP adapter."""

    async def search(
        self, *, owner_id: str, query: str, k: int = 10, memory_type: str = "all"
    ) -> list[MemoryHit]:
        """Hybrid graph+vector search over the user's long-term memory.

        memory_type: "all" | "semantic" (facts/preferences) | "episodic" (events)
        """

    async def store(
        self,
        *,
        owner_id: str,
        content: str,
        memory_type: str,
        thread_id: str | None = None,
    ) -> str:
        """Queue a memory episode for background extraction. Returns episode_id."""

    async def forget(self, *, owner_id: str, query: str) -> int:
        """Search for memories matching query and delete them. Returns count deleted."""

    async def get_context(self, *, owner_id: str) -> str:
        """Return a concise context string about the user for agent injection."""

    async def graph_search(
        self, *, owner_id: str, entity: str, k: int = 10
    ) -> list[GraphTriple]:
        """Return relationship triples for entities matching the given name."""

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
        """Upsert an entity node and optionally link it to a thread."""

    async def provision_link(
        self, *, from_id: str, to_id: str, owner_id: str, relationship: str
    ) -> None:
        """Create a directed relationship between two entity nodes."""
