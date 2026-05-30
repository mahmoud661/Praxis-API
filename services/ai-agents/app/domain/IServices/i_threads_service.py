"""DI token `"IThreadsService"` (impl class `ThreadsService`)."""

from __future__ import annotations

from typing import Protocol

from ..dtos.thread_dto import HistoryMessageView, HistoryPageView, ThreadView


class IThreadsService(Protocol):
    async def create(self, *, owner_id: str, title: str | None = None) -> ThreadView: ...

    async def list_for_owner(self, owner_id: str) -> list[ThreadView]: ...

    async def get(self, *, thread_id: str, owner_id: str) -> ThreadView: ...

    async def delete(self, *, thread_id: str, owner_id: str) -> None: ...

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
