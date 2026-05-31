"""
DI token "IThreadRepo" (impl class `ThreadRepo`).

Thread = one conversation. The repo persists the *metadata* we need to draw
the sidebar (id, owner, title, timestamps). The agent's runtime state
(message history) lives in the LangGraph checkpointer; the ThreadsService
reads from that for the history endpoint.
"""

from __future__ import annotations

from typing import Protocol

from ..dtos.thread_dto import ThreadView


class IThreadRepo(Protocol):
    async def upsert(self, thread: ThreadView) -> None:
        """Insert or update the sidebar metadata for a thread."""

    async def get(self, thread_id: str) -> ThreadView | None:
        """Return the thread's metadata, or None if it doesn't exist."""

    async def list_for_owner(self, owner_id: str) -> list[ThreadView]:
        """Every thread the owner has access to, newest activity first."""

    async def delete(self, thread_id: str) -> None:
        """Remove the thread's metadata row. Idempotent."""

    async def touch(self, thread_id: str) -> None:
        """Bump `updated_at` to now. Called when a run starts on the thread
        so the sidebar's sort-by-recent works."""
