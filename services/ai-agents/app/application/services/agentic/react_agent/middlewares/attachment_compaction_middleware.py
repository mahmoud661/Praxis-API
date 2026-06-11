"""
AttachmentCompactionMiddleware — strips attachment bytes out of old
messages before each model call, replacing them with self-describing
stubs. The model can re-fetch full bytes any time via the
`read_attachment` tool — same id, persistent on disk.

Why this exists: every model call replays the WHOLE message history.
A user-attached image carries ~1500 input tokens. After 5 turns
that's 7,500 tokens per turn, all for the SAME image. After 20 turns
the model is mostly paying for replayed bytes. This middleware caps
that growth — old attachments compress to ~30 tokens each (the stub).

Eviction is governed by `attachment_compaction_keep_turns` from env:
attachments in the LAST N user turns stay intact; everything older
gets the stub treatment. Stubs are idempotent (marked so the
middleware doesn't re-compact what it already compacted).

Captions:
  First time a file is evicted, we ask the LLM for a one-sentence
  description ("login form screenshot showing two input fields") and
  persist it on the file's metadata via `FilesService.set_caption`.
  Subsequent evictions just look up the cached caption — caption
  generation is paid at most once per file, regardless of how many
  threads/turns it appears in.

Stub shape:
  - with caption: `[Attachment cleared — was: <caption>. Re-fetch via
    read_attachment({file_id}).]`
  - fallback (caption gen failed or skipped): `[Attachment cleared —
    was a <mime> file '<filename>'. Re-fetch via read_attachment({file_id}).]`

Where the eviction lands:
  - Synthetic ToolMessage from `AttachmentPreloadMiddleware` (text
    attachments): `content` replaced with stub string.
  - Image content blocks inside a HumanMessage (`{type: "image_url",
    image_url: {...}}`): each block replaced with a `{type: "text",
    text: <stub>}` block.

Both paths leave the message structure intact — only the bytes-heavy
content changes shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import Overwrite

from ...._errors import FileNotFoundError as FileNotFoundDomainError
from ..._attachment_caption import generate_attachment_caption

if TYPE_CHECKING:

    from langgraph.runtime import Runtime

    from ......domain.IServices.i_files_service import IFilesService
    from ......domain.ports.document_extractor import IDocumentExtractor
    from ......domain.ports.logger import Logger
    from ......infrastructure.config.env import Env

_log = logging.getLogger(__name__)


# Marker stashed on a message's `additional_kwargs` after we've
# rewritten its content. Prevents re-compacting on the next model
# call (the middleware runs once per call, message history grows).
_COMPACTED_MARKER = "_attachment_compacted"


# ---- middleware --------------------------------------------------------------


class AttachmentCompactionMiddleware(AgentMiddleware):
    """Pre-model hook that strips old attachment bytes from message
    history. Runs on every model call but is idempotent — already-
    compacted messages are skipped.

    Constructor takes everything the caption generator needs (files
    service, extractor, env, logger). The compaction itself is pure
    walking + rewriting; the only async work is the LLM call to
    generate a fresh caption on first eviction of a file.
    """

    def __init__(
        self,
        files: "IFilesService",
        extractor: "IDocumentExtractor",
        env: "Env",
        logger: "Logger",
    ) -> None:
        super().__init__()
        self._files = files
        self._extractor = extractor
        self._env = env
        self._logger = logger
        self._keep_turns = env.attachment_compaction_keep_turns

    async def abefore_model(
        self,
        state: AgentState,
        runtime: "Runtime[Any]",
    ) -> dict[str, Any] | None:
        del runtime  # owner_id comes from RunnableConfig contextvar
        owner_id = _owner_id_from_config()
        messages = list(state.get("messages") or [])
        if not messages:
            return None

        # Identify the cutoff — the index of the (keep_turns)-th
        # most recent HumanMessage. Everything BEFORE it is fair game
        # for compaction; that message and everything after stays
        # full-fidelity.
        cutoff = _cutoff_index(messages, keep=self._keep_turns)
        if cutoff <= 0:
            return None  # not enough history to evict yet

        changed = False
        for idx in range(cutoff):
            msg = messages[idx]
            if _is_compacted(msg):
                continue
            replaced = await self._maybe_compact_message(
                msg, owner_id=owner_id
            )
            if replaced is not None:
                messages[idx] = replaced
                changed = True

        if not changed:
            return None

        _log.info(
            "attachment_compaction.applied cutoff=%d keep_turns=%d",
            cutoff,
            self._keep_turns,
        )
        return {"messages": Overwrite(messages)}

    # Sync mirror — production goes through the async path. Kept so a
    # sync test harness doesn't AttributeError.
    def before_model(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: "Runtime[Any]",  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return None

    # ----- compaction internals ------------------------------------------

    async def _maybe_compact_message(
        self, msg: Any, *, owner_id: str | None
    ) -> Any | None:
        """Return a rewritten message, or None if this message has
        nothing to compact. Walks two paths:

          - ToolMessage from the preload (name=read_attachment) →
            replace `content` with a stub string.
          - HumanMessage with image content blocks → replace each
            image block with a text stub block.
        """
        if isinstance(msg, ToolMessage) and msg.name == "read_attachment":
            return await self._compact_tool_message(msg, owner_id=owner_id)
        if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
            return await self._compact_human_message(msg, owner_id=owner_id)
        return None

    async def _compact_tool_message(
        self, msg: ToolMessage, *, owner_id: str | None
    ) -> ToolMessage | None:
        file_id = _tool_call_file_id_from_history(msg)
        stub = await self._stub_for_file(
            file_id=file_id, owner_id=owner_id
        )
        if stub is None:
            return None
        extras = dict(msg.additional_kwargs or {})
        extras[_COMPACTED_MARKER] = True
        return ToolMessage(
            content=stub,
            name=msg.name,
            tool_call_id=msg.tool_call_id,
            id=getattr(msg, "id", None),
            additional_kwargs=extras,
        )

    async def _compact_human_message(
        self, msg: HumanMessage, *, owner_id: str | None
    ) -> HumanMessage | None:
        """Walk the content list, swap image blocks for text stubs.
        File ids come from `msg.additional_kwargs.attachments` (the
        snapshot AgentRunner stashed at send time)."""
        attachments_meta = _attachment_snapshots(msg)
        if not attachments_meta:
            return None
        # Map ordering: the N-th image block in `content` corresponds
        # to the N-th image-MIME entry in `attachments_meta`. That's
        # the order AttachmentPreloadMiddleware inserts them.
        image_attachments = [
            a
            for a in attachments_meta
            if str(a.get("mime_type", "")).startswith("image/")
        ]
        if not image_attachments:
            return None
        new_blocks: list[Any] = []
        image_seen = 0
        any_replaced = False
        for block in msg.content:
            if (
                isinstance(block, dict)
                and block.get("type") == "image_url"
                and image_seen < len(image_attachments)
            ):
                meta = image_attachments[image_seen]
                image_seen += 1
                stub = await self._stub_for_file(
                    file_id=str(meta.get("id", "")),
                    owner_id=owner_id,
                    fallback_filename=str(meta.get("filename", "attachment")),
                    fallback_mime=str(meta.get("mime_type", "image/*")),
                )
                if stub is None:
                    new_blocks.append(block)
                    continue
                new_blocks.append({"type": "text", "text": stub})
                any_replaced = True
            else:
                new_blocks.append(block)
        if not any_replaced:
            return None
        extras = dict(msg.additional_kwargs or {})
        extras[_COMPACTED_MARKER] = True
        return HumanMessage(
            content=new_blocks,
            id=getattr(msg, "id", None),
            additional_kwargs=extras,
        )

    async def _stub_for_file(
        self,
        *,
        file_id: str | None,
        owner_id: str | None,
        fallback_filename: str = "attachment",
        fallback_mime: str = "application/octet-stream",
    ) -> str | None:
        """Build the eviction stub for one file. Caption lookup +
        lazy generation happens here.

        Returns None when we can't form a meaningful stub (no file id
        AND no owner) — caller leaves the original message alone."""
        if not file_id:
            return None
        if owner_id is None:
            # Without an owner_id we can't look the file up. Fall back
            # to a minimal stub keyed on the id alone — still tells
            # the model what it's missing and how to recover.
            return _stub(file_id=file_id, caption=None)

        caption: str | None = None
        try:
            view = await self._files.get(
                file_id=file_id, owner_id=owner_id
            )
        except FileNotFoundDomainError:
            view = None

        if view is not None:
            caption = view.caption
            if caption is None:
                # First eviction of this file — generate + persist.
                caption = await self._generate_and_cache_caption(
                    file_id=file_id, owner_id=owner_id
                )
            if caption is None:
                caption = f"a {view.mime_type} file '{view.filename}'"
        else:
            caption = f"a {fallback_mime} file '{fallback_filename}'"

        return _stub(file_id=file_id, caption=caption)

    async def _generate_and_cache_caption(
        self, *, file_id: str, owner_id: str
    ) -> str | None:
        caption = await generate_attachment_caption(
            files=self._files,
            extractor=self._extractor,
            env=self._env,
            logger=self._logger,
            file_id=file_id,
            owner_id=owner_id,
        )
        if caption:
            try:
                await self._files.set_caption(
                    file_id=file_id,
                    owner_id=owner_id,
                    caption=caption,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "compaction.caption_persist_failed",
                    file_id=file_id,
                    error=repr(exc),
                )
        return caption


# ---- module helpers ----------------------------------------------------------


def _stub(*, file_id: str, caption: str | None) -> str:
    if caption:
        return (
            f"[Attachment cleared — was: {caption}. "
            f"Re-fetch via read_attachment({file_id}).]"
        )
    return (
        f"[Attachment cleared. "
        f"Re-fetch via read_attachment({file_id}).]"
    )


def _is_compacted(msg: Any) -> bool:
    extras = getattr(msg, "additional_kwargs", None) or {}
    return bool(extras.get(_COMPACTED_MARKER))


def _cutoff_index(messages: list, *, keep: int) -> int:
    """Index of the (keep)-th most recent HumanMessage. Returns 0 if
    the thread has `keep` or fewer user turns (i.e. nothing to evict)."""
    if keep <= 0:
        return len(messages)  # evict everything
    human_indices = [
        i for i, m in enumerate(messages) if isinstance(m, HumanMessage)
    ]
    if len(human_indices) <= keep:
        return 0
    return human_indices[-keep]


def _owner_id_from_config() -> str | None:
    """Same accessor pattern as the preload middleware — read the
    live RunnableConfig from its contextvar."""
    config = var_child_runnable_config.get()
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    owner_id = configurable.get("owner_id")
    return owner_id if isinstance(owner_id, str) else None


def _tool_call_file_id_from_history(msg: ToolMessage) -> str | None:
    """Synthetic preload tool calls were created with
    `args={"file_id": <id>}` on the AIMessage's tool_calls entry.
    The ToolMessage itself doesn't carry that — but its
    `tool_call_id` matches a tool_call somewhere upstream.

    For compaction we don't need to walk backwards to recover the
    args; the file id is on the tool call's PAIRED AIMessage. But
    we'd have to scan upstream messages. As a simpler path:
    `AttachmentPreloadMiddleware` stamps the file id on
    `additional_kwargs["file_id"]` of the synthetic ToolMessage —
    see the preload code."""
    extras = getattr(msg, "additional_kwargs", None) or {}
    file_id = extras.get("file_id")
    return file_id if isinstance(file_id, str) else None


def _attachment_snapshots(msg: HumanMessage) -> list[dict[str, Any]]:
    """Pull the attachments-metadata snapshot from the message's
    additional_kwargs (the same one AgentRunner stashes for the
    frontend chip rendering). Image-MIME entries land in compaction;
    everything else is ignored here (text attachments took the
    ToolMessage path)."""
    extras = getattr(msg, "additional_kwargs", None) or {}
    raw = extras.get("attachments") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
