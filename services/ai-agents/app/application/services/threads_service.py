"""
ThreadsService — orchestrates the thread metadata repo and the LangGraph
checkpointer.

Responsibilities:

  - create / list / get / delete thread metadata (delegated to `IThreadRepo`)
  - read message history for a thread from the checkpointer
  - enforce ownership: a user can only see/touch their own threads

`load_messages` deliberately doesn't depend on the agent graph being built —
it goes straight to `AsyncPostgresSaver.aget_tuple` and pulls
`channel_values["messages"]` from the latest checkpoint. That way the
history endpoint works even if the agent definition changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from redis.asyncio import Redis

from ...domain.dtos.thread_dto import (
    HistoryAttachmentView,
    HistoryMessageView,
    HistoryPageView,
    HistoryToolCallView,
    ThreadConfigView,
    ThreadView,
)
from ...domain.IRepos.i_thread_repo import IThreadRepo
from ...domain.events.conversation_events import (
    make_conversation_deleted,
    make_conversation_renamed,
)
from ...domain.ports.event_publisher import EventPublisher
from ...domain.ports.i_memory_client import IMemoryClient
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore
from ...infrastructure.ai.title_generator import TitleGenerator
from .agentic.agent_registry import AgentRegistry
from ._errors import InvalidThreadConfigError, ThreadNotFoundError  # noqa: F401  (re-exports)

_AGENTS_TOPIC = "agents.events.v1"


# Default title used for freshly-created threads — matches ThreadsService.create
# below. The auto-titler only runs when a thread is still on this default so we
# never overwrite a name the user (or anyone else) set explicitly.
_DEFAULT_TITLE = "New conversation"


class ThreadsService:
    """Auto-bound to the DI token `"IThreadsService"`."""

    def __init__(
        self,
        thread_repo: IThreadRepo,
        agentic_store: AgenticStore,
        title_generator: TitleGenerator,
        redis: Redis,
        logger: Logger,
        agent_registry: AgentRegistry,
        event_publisher: EventPublisher,
        memory_client: IMemoryClient,
    ) -> None:
        # Resolved by the container by annotation class name.
        self._repo = thread_repo
        self._agentic = agentic_store
        self._title_gen = title_generator
        self._redis = redis
        self._logger = logger
        self._registry = agent_registry
        self._publisher = event_publisher
        self._memory = memory_client

    async def create(
        self, *, owner_id: str, title: str | None = None
    ) -> ThreadView:
        now = _now_iso()
        thread = ThreadView(
            id=str(uuid4()),
            owner_id=owner_id,
            title=(title or "New conversation"),
            created_at=now,
            updated_at=now,
        )
        await self._repo.upsert(thread)
        # Register the conversation node directly in the knowledge graph.
        # Fire-and-forget: a failure here must not break thread creation.
        try:
            await self._memory.provision_node(
                type="conversation",
                id=thread.id,
                name=thread.title,
                owner_id=owner_id,
                summary="AI conversation thread",
            )
            await self._memory.provision_link(
                from_id=owner_id,
                to_id=thread.id,
                owner_id=owner_id,
                relationship="PARTICIPATED_IN",
            )
        except Exception:  # noqa: BLE001
            self._logger.warning("thread.provision_memory_failed", thread_id=thread.id)
        self._logger.info(
            "thread.created", thread_id=thread.id, owner_id=owner_id
        )
        return thread

    async def list_for_owner(self, owner_id: str) -> list[ThreadView]:
        return await self._repo.list_for_owner(owner_id)

    async def get(self, *, thread_id: str, owner_id: str) -> ThreadView:
        thread = await self._repo.get(thread_id)
        if thread is None or thread.owner_id != owner_id or thread.deleted_at is not None:
            raise ThreadNotFoundError(thread_id)
        return thread

    async def delete(self, *, thread_id: str, owner_id: str) -> None:
        # Ownership check first — same "404 on miss-or-foreign" rule.
        await self.get(thread_id=thread_id, owner_id=owner_id)
        deleted_at = _now_iso()
        await self._repo.soft_delete(thread_id)
        # Notify the memory service so it can mark the Conversation node
        # as disabled in Neo4j. Best-effort — a Kafka blip must not fail
        # the delete response.
        try:
            await self._publisher.publish(
                _AGENTS_TOPIC,
                [make_conversation_deleted(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    deleted_at=deleted_at,
                )],
            )
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "thread.deleted.kafka_publish_failed",
                thread_id=thread_id,
                error=str(err),
            )
        self._logger.info(
            "thread.deleted", thread_id=thread_id, owner_id=owner_id
        )

    async def update_config(
        self,
        *,
        thread_id: str,
        owner_id: str,
        config: ThreadConfigView,
    ) -> ThreadView:
        """Validate the new config against the agent registry, then
        persist it. Raises:
          - `ThreadNotFoundError` if the thread doesn't exist OR the
            caller doesn't own it (same hidden-existence rule as `get`).
          - `InvalidThreadConfigError` if `agent_id` is unknown OR a
            `tool_overrides` entry refers to a tool the agent doesn't
            expose OR a tool the agent declared non-toggleable.
        """
        # Ownership check — 404 on miss-or-foreign.
        await self.get(thread_id=thread_id, owner_id=owner_id)

        self._validate_config(config)
        updated = await self._repo.update_config(thread_id, config)
        if updated is None:
            # Race: thread deleted between the ownership check and the
            # write. Treat as not-found.
            raise ThreadNotFoundError(thread_id)
        self._logger.info(
            "thread.config_updated",
            thread_id=thread_id,
            owner_id=owner_id,
            agent_id=config.agent_id,
            override_count=len(config.tool_overrides),
        )
        return updated

    def _validate_config(self, config: ThreadConfigView) -> None:
        # No agent_id → use account default; nothing to validate yet.
        agent = (
            self._registry.get(config.agent_id) if config.agent_id else None
        )
        if config.agent_id and agent is None:
            raise InvalidThreadConfigError(
                f"unknown agent_id {config.agent_id!r}"
            )
        # If no overrides, we're done.
        if not config.tool_overrides:
            return
        # Overrides are only meaningful in the context of a known agent.
        # When the thread has no explicit agent_id, validate against
        # the default agent — that's the one the resolver will pick.
        if agent is None:
            default = self._registry.get(self._registry.default_id())
            if default is None:
                # Defensive: registry boot validation should make this
                # impossible.
                raise InvalidThreadConfigError(
                    "registry has no default agent to validate overrides against"
                )
            agent = default
        tools_by_id = {t.id: t for t in agent.spec.tools}
        for tool_id in config.tool_overrides:
            tool = tools_by_id.get(tool_id)
            if tool is None:
                raise InvalidThreadConfigError(
                    f"agent {agent.spec.id!r} has no tool {tool_id!r}"
                )
            if not tool.user_toggleable:
                raise InvalidThreadConfigError(
                    f"tool {tool_id!r} on agent {agent.spec.id!r} is not "
                    f"user-toggleable; remove the override"
                )

    async def load_messages(
        self, *, thread_id: str, owner_id: str
    ) -> list[HistoryMessageView]:
        # Ownership first — don't leak history to anyone else.
        await self.get(thread_id=thread_id, owner_id=owner_id)

        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await self._agentic.checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            return []

        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", []) or []
        return _pair_messages_for_view(messages)

    async def maybe_generate_title(
        self, *, thread_id: str, owner_id: str
    ) -> str | None:
        """Auto-title a brand-new thread from its first user message,
        streaming the title into the sidebar token-by-token.

        Idempotent and best-effort:
          - Returns None and does nothing if the thread already has a
            non-default title (a user rename, or a previous auto-title).
          - Returns None on any LLM or persistence failure. Caller should
            not depend on a title materialising.
          - Publishes ``thread.title.delta`` for every cumulative
            sanitized snapshot as the LLM streams, then a final
            ``thread.title.updated`` once the model completes. The
            frontend treats both shapes the same way (replace title).

        Called by RunManager as a post-turn background task. The first
        call after the user's opening message names the thread; every
        subsequent call short-circuits on the non-default title check.
        """
        try:
            thread = await self.get(thread_id=thread_id, owner_id=owner_id)
        except ThreadNotFoundError:
            return None

        if (thread.title or "").strip() != _DEFAULT_TITLE:
            return None  # already named — never overwrite

        messages = await self.load_messages(
            thread_id=thread_id, owner_id=owner_id
        )
        first_user = next(
            (m for m in messages if m.role == "user" and m.content.strip()),
            None,
        )
        if first_user is None:
            return None

        # Stream the title, publishing every cumulative snapshot. The
        # generator yields ALREADY-sanitized text, so each chunk is
        # safe to display directly in the sidebar without flicker from
        # stray quotes / markdown / trailing punctuation.
        final_title: str | None = None
        try:
            async for snapshot in self._title_gen.stream(
                user_message=first_user.content
            ):
                final_title = snapshot
                await self._publish_thread_event(
                    owner_id,
                    {
                        "type": "thread.title.delta",
                        "thread_id": thread_id,
                        "title": snapshot,
                    },
                )
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "thread.title.stream_failed",
                thread_id=thread_id,
                error=str(err),
            )
            return None

        if not final_title or final_title == _DEFAULT_TITLE:
            return None

        updated = ThreadView(
            id=thread.id,
            owner_id=thread.owner_id,
            title=final_title,
            created_at=thread.created_at,
            updated_at=_now_iso(),
        )
        try:
            await self._repo.upsert(updated)
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "thread.title.persist_failed",
                thread_id=thread_id,
                error=str(err),
            )
            return None

        # Keep the knowledge graph node name in sync with the new title.
        # Best-effort — a Kafka blip must not fail title generation.
        try:
            await self._publisher.publish(
                _AGENTS_TOPIC,
                [make_conversation_renamed(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    title=final_title,
                )],
            )
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "thread.title.kafka_publish_failed",
                thread_id=thread_id,
                error=str(err),
            )

        # Terminal "this is the final title" — lets the frontend mark
        # the streaming animation as done and stop accepting future
        # deltas for this thread.
        await self._publish_thread_event(
            owner_id,
            {
                "type": "thread.title.updated",
                "thread_id": thread_id,
                "title": final_title,
            },
        )

        self._logger.info(
            "thread.title.generated", thread_id=thread_id, title=final_title
        )
        return final_title

    async def _publish_thread_event(
        self, owner_id: str, payload: dict
    ) -> None:
        """Publish a thread-scoped event on the owner's notif channel.
        Tolerates Redis blips — if the publish fails, the sidebar
        catches up on the next hydrate."""
        try:
            await self._redis.publish(
                f"users:{owner_id}:notif",
                json.dumps({**payload, "at": _now_iso()}),
            )
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "thread.event.notify_failed",
                event=payload.get("type"),
                error=str(err),
            )

    async def load_messages_page(
        self,
        *,
        thread_id: str,
        owner_id: str,
        limit: int,
        before: str | None,
    ) -> HistoryPageView:
        """Cursor-paginated history.

        Order: the returned slice is always in chronological order
        (oldest → newest). The "page" is the most recent N when
        `before` is None; otherwise it's the N messages immediately
        before the cursor (so the frontend can prepend them on
        scroll-up).
        """
        all_messages = await self.load_messages(
            thread_id=thread_id, owner_id=owner_id
        )

        # Clamp limit defensively — keeps the network payload bounded
        # even if a client tries to request the whole history at once.
        bounded = max(1, min(limit, 200))

        if before is None:
            # First page = the tail of the history.
            end = len(all_messages)
        else:
            # Find the cursor (id of an older oldest-loaded message)
            # and slice the page that ends just before it. If the
            # cursor isn't found (stale id, race with delete) treat
            # it as "no older page available".
            cursor_idx = next(
                (i for i, m in enumerate(all_messages) if m.id == before),
                None,
            )
            if cursor_idx is None or cursor_idx == 0:
                return HistoryPageView(
                    messages=[], has_more=False, next_cursor=None
                )
            end = cursor_idx

        start = max(0, end - bounded)
        page = all_messages[start:end]
        has_more = start > 0
        next_cursor = page[0].id if has_more and page else None
        return HistoryPageView(
            messages=page,
            has_more=has_more,
            next_cursor=next_cursor,
        )


def _pair_messages_for_view(
    raw_messages: list[object],
) -> list[HistoryMessageView]:
    """Take the raw LangChain message list out of the checkpointer and
    return a UI-friendly history list.

    What this does:
      1. Scans for ToolMessages and indexes them by their tool_call_id.
      2. Walks the messages in order, emitting one HistoryMessageView per
         user/assistant message. For each AIMessage, attaches its
         tool_calls — pre-resolved with the results we found in step 1.
      3. Drops standalone ToolMessages from the list (they're shown via
         the parent assistant's tool-call cards now, not as their own
         turns).
      4. Drops messages that have nothing visible — no content AND no
         tool calls.

    The end result: the frontend gets exactly the same logical shape
    it builds during a live stream — assistant messages with their
    `toolCalls[]` populated and resolved.
    """
    tool_results: dict[str, str] = {}
    for msg in raw_messages:
        if getattr(msg, "type", "") == "tool":
            tcid = getattr(msg, "tool_call_id", None)
            if tcid:
                content = getattr(msg, "content", "") or ""
                tool_results[str(tcid)] = _flatten_content(content)

    out: list[HistoryMessageView] = []
    for msg in raw_messages:
        msg_type = getattr(msg, "type", "") or ""
        if msg_type == "tool":
            continue  # surfaced via the parent AIMessage's tool_calls
        view = _to_history_view(msg, tool_results)
        # Keep the message if it has ANYTHING the UI can render:
        # visible text, tool calls (assistant turns), OR attachments
        # (user turns that uploaded a file with no caption text). The
        # attachments check is critical — user-attached images have
        # their content rewritten to a list of `image_url` blocks by
        # the preload middleware, which the flattener drops, leaving
        # `content` empty. Without this check, the user's "look at
        # this image" bubble would disappear from history.
        if view.content.strip() or view.tool_calls or view.attachments:
            out.append(view)
    return out


def _to_history_view(
    msg: object, tool_results: dict[str, str]
) -> HistoryMessageView:
    """Best-effort mapping from LangChain BaseMessage to our view DTO."""
    msg_type = getattr(msg, "type", "") or ""
    role = _ROLE_BY_TYPE.get(msg_type, msg_type or "assistant")
    content = _flatten_content(getattr(msg, "content", "") or "")
    msg_id = str(getattr(msg, "id", "") or "")

    tool_calls: list[HistoryToolCallView] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        # tool_calls entries are usually dicts with id/name/args.
        if isinstance(tc, dict):
            tc_id = str(tc.get("id") or "")
            tc_name = str(tc.get("name") or "")
            tc_args_raw = tc.get("args")
        else:
            tc_id = str(getattr(tc, "id", "") or "")
            tc_name = str(getattr(tc, "name", "") or "")
            tc_args_raw = getattr(tc, "args", None)
        # Synthetic preload calls (AttachmentPreloadMiddleware fabricates
        # them with a `preload-` id prefix) are model-facing plumbing —
        # the user already sees the attachment as a chip on their own
        # message, so a "read_attachment" tool card for it is noise.
        # Filtering here also drops the carrier AIMessage entirely: its
        # content is empty, so with zero visible tool calls it fails the
        # keep-check in `_pair_messages_for_view`.
        if tc_id.startswith("preload-"):
            continue
        tc_args = tc_args_raw if isinstance(tc_args_raw, dict) else {}
        tool_calls.append(
            HistoryToolCallView(
                id=tc_id,
                name=tc_name,
                args=tc_args,
                result=tool_results.get(tc_id),
            )
        )

    attachments = _attachments_from_msg(msg)
    content_refs = _content_references_from_msg(msg)
    return HistoryMessageView(
        id=msg_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        attachments=attachments,
        content_references=content_refs,
    )


def _content_references_from_msg(msg: object) -> list[dict]:
    """Pull resolved content references off an assistant message. The
    backend's `ContentReferenceMiddleware` stamps these onto
    `additional_kwargs.content_references` after each model emission.
    History reload just passes them straight through."""
    extras = getattr(msg, "additional_kwargs", None) or {}
    raw = extras.get("content_references") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _attachments_from_msg(msg: object) -> list[HistoryAttachmentView]:
    """Pull attachment snapshots out of `HumanMessage.additional_kwargs`.
    Returns `[]` for non-user messages and messages without attachments.
    Defensive: malformed entries (missing fields, wrong types) are
    silently dropped rather than crashing the history render."""
    extras = getattr(msg, "additional_kwargs", None) or {}
    raw = extras.get("attachments") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[HistoryAttachmentView] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                HistoryAttachmentView(
                    id=str(item["id"]),
                    filename=str(item.get("filename", "")),
                    mime_type=str(item.get("mime_type", "application/octet-stream")),
                    size_bytes=int(item.get("size_bytes", 0) or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _flatten_content(content: object) -> str:
    """LangChain sometimes packs content as a list of content-blocks.
    Flatten naively to a string the frontend can render.

    Non-text blocks (e.g. `{type: "image_url", image_url: {...}}` from
    the synthetic read_attachment preload) have no `text` field — they
    drop out of the flattened string rather than serializing as
    `"None"` or raising. The frontend renders attachments via
    `additional_kwargs.attachments` anyway, so dropping image blocks
    here doesn't lose information.

    Plumbing-text guard: some text blocks inside a HumanMessage are
    middleware plumbing, not the user's words, and must not leak into
    the rendered bubble:
      - `[Attached image aliases: …]` — an early preload version.
      - `[Attachment cleared — …]` / `[Attachment cleared.` — the
        AttachmentCompactionMiddleware rewrites old image blocks into
        these eviction stubs IN PERSISTED STATE, so on history reload
        they'd otherwise appear as the user's own text (with the raw
        file UUID).
    Both are skipped here so the bubble shows only what the user typed;
    the attachment chip still renders from `additional_kwargs.attachments`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and not _is_plumbing_text(text):
                    parts.append(text)
        return "".join(parts)
    return str(content) if content is not None else ""


# Prefixes that mark a text block as middleware plumbing rather than
# the user's own words (see `_flatten_content`).
_PLUMBING_TEXT_PREFIXES = (
    "[Attached image aliases:",
    "[Attachment cleared",
)


def _is_plumbing_text(text: str) -> bool:
    return text.startswith(_PLUMBING_TEXT_PREFIXES)


_ROLE_BY_TYPE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "AIMessageChunk": "assistant",
    "system": "system",
    "tool": "tool",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
