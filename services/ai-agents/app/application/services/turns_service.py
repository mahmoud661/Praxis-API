"""
TurnsService — implements "retry" and "edit" actions on a thread.

What these mean for the user:

  - Retry: the user clicks the regenerate button on an assistant reply.
    The agent re-runs from the prior user message; a fresh assistant
    response replaces the old one.

  - Edit: the user clicks the pencil on one of their own messages,
    rewrites it, and hits save. Everything from that message onward is
    discarded and the agent runs against the new text.

How that maps to LangGraph
--------------------------

The compiled react-agent graph keeps a `messages` channel in its state
— a list of `BaseMessage` objects, each with a UUID `.id`. The
checkpointer (`AsyncPostgresSaver`) persists this state per thread.

To rewind we use `graph.aupdate_state(config, {"messages": [...]})`
with a list of `RemoveMessage` items. The reducer on the messages
channel treats `RemoveMessage` as a delete-by-id directive — the
matching messages are dropped from state, leaving everything before
intact. After that we kick a fresh run through `RunManager` exactly as
if the user had just typed the (new or original) text.

Both operations require:

  - thread ownership (we don't let user A rewind user B's chat)
  - no in-flight run on the thread (rewinding mid-stream would race
    against the running agent task)
  - the target message to be present in current state AND to be a user
    message (assistant messages don't define a rollback point)
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import RemoveMessage

from ...domain.IRepos.i_thread_repo import IThreadRepo
from ...domain.ports.logger import Logger
from .agentic.agent_registry import AgentRegistry
from .agentic.run_manager import RunManager
from ._errors import (
    InvalidTurnTargetError,
    MessageNotFoundError,
    ThreadNotFoundError,
    TurnInProgressError,
)


_USER_MSG_TYPES = {"human", "user"}


class TurnsService:
    """Auto-bound to the DI token `"ITurnsService"`."""

    def __init__(
        self,
        thread_repo: IThreadRepo,
        agent_registry: AgentRegistry,
        run_manager: RunManager,
        logger: Logger,
    ) -> None:
        self._repo = thread_repo
        self._registry = agent_registry
        self._run_manager = run_manager
        self._logger = logger

    async def retry(
        self, *, thread_id: str, owner_id: str, message_id: str
    ) -> None:
        """Rewind to just before `message_id` (a user message) and re-run
        with the SAME content. Used by the "regenerate" button."""
        await self._rewind_and_run(
            thread_id=thread_id,
            owner_id=owner_id,
            message_id=message_id,
            content=None,
            action="retry",
        )

    async def edit(
        self,
        *,
        thread_id: str,
        owner_id: str,
        message_id: str,
        content: str,
    ) -> None:
        """Rewind to just before `message_id` and re-run with NEW content.
        Used by the user-message inline edit."""
        new_content = content.strip()
        if not new_content:
            raise InvalidTurnTargetError("edit content is empty")
        await self._rewind_and_run(
            thread_id=thread_id,
            owner_id=owner_id,
            message_id=message_id,
            content=new_content,
            action="edit",
        )

    # ----- internals -------------------------------------------------------

    async def _rewind_and_run(
        self,
        *,
        thread_id: str,
        owner_id: str,
        message_id: str,
        content: str | None,
        action: str,
    ) -> None:
        await self._ensure_ownership(thread_id, owner_id)

        if self._run_manager.is_active(thread_id):
            raise TurnInProgressError(thread_id)

        graph = self._registry.default_agent().get()
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        messages = list(state.values.get("messages") or [])

        # Find the message we're rewinding from. We match on the `.id`
        # UUID that LangChain assigns when the message was first added.
        target_idx = -1
        for idx, msg in enumerate(messages):
            if str(getattr(msg, "id", "")) == message_id:
                target_idx = idx
                break
        if target_idx < 0:
            raise MessageNotFoundError(message_id)

        target = messages[target_idx]
        msg_type = getattr(target, "type", "")
        if msg_type not in _USER_MSG_TYPES:
            raise InvalidTurnTargetError(
                f"{action} target must be a user message; got {msg_type!r}",
            )

        # For retry, the content is whatever the user originally typed.
        # The persisted HumanMessage content may be a LIST of content
        # blocks — AttachmentPreloadMiddleware rewrites it that way when
        # the turn carried images (text block + image_url blocks). We
        # must extract ONLY the text, never `str(list)` — that would
        # post the Python repr of the block list (base64 data URLs and
        # all) as the regenerated user message.
        if content is None:
            content = _user_text(getattr(target, "content", "")).strip()
        if not content:
            raise InvalidTurnTargetError(f"{action} content resolved to empty")

        # Preserve the turn's attachments. The original HumanMessage
        # carries a metadata snapshot under `additional_kwargs.attachments`
        # (written by AgentRunner); re-running without the file ids would
        # silently drop the files from the regenerated turn — the preload
        # middleware only primes the model with files listed in config.
        attachment_ids = _attachment_ids(target)

        # Delete the target user message and everything that followed.
        # The reducer on the messages channel handles RemoveMessage as
        # "drop the entry whose id matches mine".
        to_remove = [
            RemoveMessage(id=str(m.id))
            for m in messages[target_idx:]
            if getattr(m, "id", None)
        ]
        if to_remove:
            await graph.aupdate_state(config, {"messages": to_remove})

        self._logger.info(
            "turn.rewound",
            thread_id=thread_id,
            action=action,
            removed=len(to_remove),
            from_index=target_idx,
        )

        # Kick the new run. RunManager handles the WS notification, the
        # event stream, and the per-thread asyncio task — same as a
        # normal "user typed and submitted" flow.
        await self._run_manager.start_run(
            thread_id=thread_id,
            owner_id=owner_id,
            content=content,
            attachments=attachment_ids,
        )

    async def _ensure_ownership(self, thread_id: str, owner_id: str) -> None:
        thread = await self._repo.get(thread_id)
        if thread is None or thread.owner_id != owner_id:
            raise ThreadNotFoundError(thread_id)


def _user_text(content: Any) -> str:
    """Pull the user's typed text out of a HumanMessage's content.

    `content` is a plain string for text-only turns, or a list of
    content blocks once the preload middleware injected image blocks.
    For the list case we concatenate only the text blocks and drop
    everything else (image_url blocks etc.) — the image is re-attached
    separately via the preserved attachment ids, so it must not bleed
    into the user message text as a base64 repr."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _attachment_ids(message: Any) -> list[str]:
    """File ids from the message's persisted attachment snapshot.
    Missing / malformed snapshot → empty list (text-only turn)."""
    extras = getattr(message, "additional_kwargs", None) or {}
    raw = extras.get("attachments") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            file_id = item.get("id")
            if isinstance(file_id, str) and file_id:
                ids.append(file_id)
    return ids
