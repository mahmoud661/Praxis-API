"""DI token `"IMemoryClient"` — port for the memory service."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryHit:
    excerpt: str
    score: float
    source: str
    entities: list[str] = field(default_factory=list)


class IMemoryClient(Protocol):
    async def search(
        self, *, owner_id: str, query: str, k: int = 10
    ) -> list[MemoryHit]:
        """Hybrid graph+vector search over the user's long-term memory."""

    async def store(
        self, *, owner_id: str, content: str, memory_type: str, thread_id: str | None = None
    ) -> str:
        """Persist a memory episode. Returns the assigned episode_id."""

    async def forget(self, *, owner_id: str, query: str) -> int:
        """Search for memories matching query and delete them. Returns count deleted."""

    async def provision_node(
        self, *, type: str, id: str, name: str, owner_id: str, summary: str = "", thread_id: str | None = None
    ) -> None:
        """Upsert an entity node and optionally link it to a thread."""

    async def provision_link(
        self, *, from_id: str, to_id: str, owner_id: str, relationship: str
    ) -> None:
        """Create a directed relationship between two entity nodes."""

    async def clear(self, *, owner_id: str) -> None:
        """Wipe all memory episodes and entities for this user."""
