"""
ThreadRepo — persists thread metadata via LangGraph's `AsyncPostgresStore`
(the same Postgres-backed store we use for checkpoints).

Layout in the Store:

    namespace = ("threads",)
    key       = thread_id   (whatever the client generated)
    value     = { owner_id, title, created_at, updated_at }

`list_for_owner` does a server-side `filter={"owner_id": …}` so we don't
pull every thread to filter in Python. The store's index over JSON values
handles this efficiently enough for the kind of fleet sizes this service
runs at — if it ever gets hot we add a dedicated index.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ....domain.dtos.thread_dto import ThreadView
from ...agentic.agentic_store import AgenticStore

_NAMESPACE = ("threads",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ThreadRepo:
    def __init__(self, agentic_store: AgenticStore) -> None:
        # Resolved by the container under token "AgenticStore".
        self._agentic = agentic_store

    async def upsert(self, thread: ThreadView) -> None:
        await self._agentic.store.aput(
            _NAMESPACE,
            thread.id,
            {
                "owner_id": thread.owner_id,
                "title": thread.title,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
            },
        )

    async def get(self, thread_id: str) -> ThreadView | None:
        item = await self._agentic.store.aget(_NAMESPACE, thread_id)
        if item is None:
            return None
        return _to_view(item.key, item.value)

    async def list_for_owner(self, owner_id: str) -> list[ThreadView]:
        items = await self._agentic.store.asearch(
            _NAMESPACE,
            filter={"owner_id": owner_id},
            limit=1000,
        )
        out = [_to_view(it.key, it.value) for it in items]
        # Newest activity first — the sidebar wants `ORDER BY updated_at DESC`.
        out.sort(key=lambda t: t.updated_at, reverse=True)
        return out

    async def delete(self, thread_id: str) -> None:
        await self._agentic.store.adelete(_NAMESPACE, thread_id)

    async def touch(self, thread_id: str) -> None:
        item = await self._agentic.store.aget(_NAMESPACE, thread_id)
        if item is None:
            return
        await self._agentic.store.aput(
            _NAMESPACE,
            thread_id,
            {**item.value, "updated_at": _now_iso()},
        )


def _to_view(key: str, value: dict[str, object]) -> ThreadView:
    return ThreadView(
        id=key,
        owner_id=str(value.get("owner_id", "")),
        title=str(value.get("title", "New conversation")),
        created_at=str(value.get("created_at", _now_iso())),
        updated_at=str(value.get("updated_at", _now_iso())),
    )
