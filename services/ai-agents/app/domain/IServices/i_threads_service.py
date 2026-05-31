"""DI token `"IThreadsService"` (impl class `ThreadsService`)."""

from __future__ import annotations

from typing import Protocol

from ..dtos.thread_dto import HistoryMessageView, HistoryPageView, ThreadView


class IThreadsService(Protocol):
    async def create(self, *, owner_id: str, title: str | None = None) -> ThreadView:
        """Provision a new thread owned by `owner_id`. Title defaults to a
        placeholder that `maybe_generate_title` later overwrites."""

    async def list_for_owner(self, owner_id: str) -> list[ThreadView]:
        """Every thread the owner can see, newest-first."""

    async def get(self, *, thread_id: str, owner_id: str) -> ThreadView:
        """Single-thread fetch. Raises if the thread doesn't exist or isn't
        owned by `owner_id` — the access check is part of the contract."""

    async def delete(self, *, thread_id: str, owner_id: str) -> None:
        """Drop the thread + its checkpointer state. Same access check as
        `get`; idempotent on the repo side."""

    async def load_messages(
        self, *, thread_id: str, owner_id: str
    ) -> list[HistoryMessageView]:
        """Read message history from the LangGraph checkpointer. Latest
        checkpoint's `messages` state, mapped to a stable role/content shape."""

    async def load_messages_page(
        self,
        *,
        thread_id: str,
        owner_id: str,
        limit: int,
        before: str | None,
    ) -> HistoryPageView:
        """Cursor-paginated history. See implementation for semantics."""

    async def maybe_generate_title(
        self, *, thread_id: str, owner_id: str
    ) -> str | None:
        """Auto-name a thread from its first user message. No-op if the
        thread already has a non-default title. See implementation."""
