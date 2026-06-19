"""
ThreadRepo — persists thread metadata via LangGraph's `AsyncPostgresStore`
(the same Postgres-backed store we use for checkpoints).

Layout in the Store:

    namespace = ("threads",)
    key       = thread_id   (whatever the client generated)
    value     = {
        owner_id, title, created_at, updated_at,
        config: { agent_id, tool_overrides, custom_system_prompt_id },
    }

`config` is the per-thread capability override layer (agent choice +
tool toggles). Missing on threads that pre-date the field — `_to_view`
defaults it to `EMPTY_CONFIG` so older threads keep working.

`list_for_owner` does a server-side `filter={"owner_id": …}` so we don't
pull every thread to filter in Python. The store's index over JSON values
handles this efficiently enough for the kind of fleet sizes this service
runs at — if it ever gets hot we add a dedicated index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ....domain.dtos.thread_dto import EMPTY_CONFIG, ThreadConfigView, ThreadView
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
            _value_from_view(thread),
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
        # Exclude soft-deleted threads from normal listings.
        out = [t for t in out if t.deleted_at is None]
        # Newest activity first — the sidebar wants `ORDER BY updated_at DESC`.
        out.sort(key=lambda t: t.updated_at, reverse=True)
        return out

    async def delete(self, thread_id: str) -> None:
        await self._agentic.store.adelete(_NAMESPACE, thread_id)

    async def soft_delete(self, thread_id: str) -> None:
        item = await self._agentic.store.aget(_NAMESPACE, thread_id)
        if item is None:
            return
        await self._agentic.store.aput(
            _NAMESPACE,
            thread_id,
            {**item.value, "deleted_at": _now_iso()},
        )

    async def touch(self, thread_id: str) -> None:
        item = await self._agentic.store.aget(_NAMESPACE, thread_id)
        if item is None:
            return
        await self._agentic.store.aput(
            _NAMESPACE,
            thread_id,
            {**item.value, "updated_at": _now_iso()},
        )

    async def update_config(
        self,
        thread_id: str,
        config: ThreadConfigView,
    ) -> ThreadView | None:
        """Persist a new `config` block, leaving every other field
        untouched. Returns the refreshed view, or `None` when the
        thread doesn't exist. Bumps `updated_at` so the sidebar
        sort-by-recent surfaces the change."""
        item = await self._agentic.store.aget(_NAMESPACE, thread_id)
        if item is None:
            return None
        new_value = {
            **item.value,
            "config": _config_to_dict(config),
            "updated_at": _now_iso(),
        }
        await self._agentic.store.aput(_NAMESPACE, thread_id, new_value)
        return _to_view(thread_id, new_value)


def _value_from_view(thread: ThreadView) -> dict[str, Any]:
    out: dict[str, Any] = {
        "owner_id": thread.owner_id,
        "title": thread.title,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "config": _config_to_dict(thread.config),
    }
    if thread.deleted_at is not None:
        out["deleted_at"] = thread.deleted_at
    return out


def _config_to_dict(config: ThreadConfigView) -> dict[str, Any]:
    return {
        "agent_id": config.agent_id,
        "tool_overrides": dict(config.tool_overrides),
        "custom_system_prompt_id": config.custom_system_prompt_id,
    }


def _config_from_dict(raw: object) -> ThreadConfigView:
    if not isinstance(raw, dict):
        return EMPTY_CONFIG
    overrides = raw.get("tool_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    # Defensive coercion: anything non-bool gets dropped, so a
    # malformed stored value can't crash the resolver later.
    cleaned: dict[str, bool] = {
        k: bool(v) for k, v in overrides.items() if isinstance(k, str)
    }
    agent_id = raw.get("agent_id")
    prompt_id = raw.get("custom_system_prompt_id")
    return ThreadConfigView(
        agent_id=agent_id if isinstance(agent_id, str) else None,
        tool_overrides=cleaned,
        custom_system_prompt_id=(
            prompt_id if isinstance(prompt_id, str) else None
        ),
    )


def _to_view(key: str, value: dict[str, object]) -> ThreadView:
    raw_deleted = value.get("deleted_at")
    return ThreadView(
        id=key,
        owner_id=str(value.get("owner_id", "")),
        title=str(value.get("title", "New conversation")),
        created_at=str(value.get("created_at", _now_iso())),
        updated_at=str(value.get("updated_at", _now_iso())),
        config=_config_from_dict(value.get("config")),
        deleted_at=str(raw_deleted) if raw_deleted is not None else None,
    )
