"""
ContentReferenceLookupService — the concrete `IContentReferenceLookup`.

Resolves model-emitted aliases (`turn3image1`, `citeturn0search2`)
back to durable entities by walking the thread's LangGraph state:

  - `turn{N}{file|image|pdf|...}{M}` →
      `HumanMessage[#N].additional_kwargs.attachments[M-of-category]`
  - `turn{N}{search|news}{M}` →
      structured `kb_search` hits the tool stashed under
      `("kb_search_hits",) → {thread_id}:{tool_call_id}` keyed by the
      tool call surfacing this turn

Ownership enforced — cross-user lookups return `None` so the resolver
silently drops the alias and the frontend renders it as plain text.

Auto-bound to the DI token `"IContentReferenceLookup"`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .agentic.main_agent import MainAgent
from .agentic.tools.kb_search import _KB_HITS_NAMESPACE
from ...domain.dtos.content_reference_dto import AttachmentRef, WebpageRef
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore


# ---- service -----------------------------------------------------------------


class ContentReferenceLookupService:
    """Auto-bound to the DI token `"IContentReferenceLookup"`."""

    def __init__(
        self,
        main_agent: MainAgent,
        agentic_store: AgenticStore,
        logger: Logger,
    ) -> None:
        self._main_agent = main_agent
        self._agentic = agentic_store
        self._logger = logger

    async def resolve_attachment(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> AttachmentRef | None:
        # Aliases are 1-indexed on the model side (`turn3image1`,
        # `turn3image2`, ...). Convert to 0-indexed for array lookup.
        # `turn3image0` is treated as "first item" (matches what a
        # forgiving parser would do).
        zero_idx = max(item_index - 1, 0)
        messages = await self._thread_messages(thread_id=thread_id)
        human = _nth_human_message(messages, turn_index)
        if human is None:
            return None
        attachments = _attachments_of_msg(human)
        filtered = [a for a in attachments if _matches_attachment_category(a, category)]
        if zero_idx >= len(filtered):
            return None
        meta = filtered[zero_idx]
        # Ownership guard — attachments meta carries owner via the
        # original AgentRunner snapshot, but a defensive lookup
        # against owner_id from the live request belt-and-braces it.
        if str(meta.get("owner_id", owner_id)) != owner_id:
            return None
        return AttachmentRef(
            file_id=str(meta.get("id", "")),
            filename=str(meta.get("filename", "")),
            mime_type=str(meta.get("mime_type", "application/octet-stream")),
            size_bytes=int(meta.get("size_bytes", 0) or 0),
        )

    async def resolve_webpage(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> WebpageRef | None:
        del owner_id, category, turn_index  # see resolution comment below
        # Citations from kb_search aren't strictly keyed by turn — the
        # tool uses `turn0search{n}` regardless of which user turn
        # triggered the call. We resolve by finding the most recent
        # kb_search tool call in the thread's history and returning
        # its item_index'th hit. The model rarely calls kb_search more
        # than once per response; if it ever does, future iterations
        # can disambiguate by tool_call_id encoded in the alias.
        messages = await self._thread_messages(thread_id=thread_id)
        tool_call_id = _latest_kb_search_tool_call_id(messages)
        if not tool_call_id:
            return None
        hits = await self._load_kb_hits(
            thread_id=thread_id, tool_call_id=tool_call_id
        )
        zero_idx = max(item_index - 1, 0)
        if zero_idx >= len(hits):
            return None
        hit = hits[zero_idx]
        return WebpageRef(
            title=str(hit.get("title", "")),
            url=str(hit.get("url", "")),
            attribution=_optional_str(hit.get("attribution")),
            snippet=_optional_str(hit.get("snippet")),
        )

    # ----- internals --------------------------------------------------------

    async def _thread_messages(self, *, thread_id: str) -> list[Any]:
        """Read the current `messages` channel from the compiled
        agent's state for one thread. Returns `[]` if the thread
        doesn't exist or has no messages — the caller treats both as
        "nothing to resolve"."""
        try:
            graph = self._main_agent.get()
            state = await graph.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "content_ref_lookup.state_fetch_failed",
                thread_id=thread_id,
                error=repr(exc),
            )
            return []
        return list(state.values.get("messages") or [])

    async def _load_kb_hits(
        self, *, thread_id: str, tool_call_id: str
    ) -> list[dict[str, Any]]:
        key = f"{thread_id}:{tool_call_id}"
        item = await self._agentic.store.aget(_KB_HITS_NAMESPACE, key)
        if item is None or not isinstance(item.value, dict):
            return []
        raw = item.value.get("hits")
        if not isinstance(raw, list):
            return []
        return [h for h in raw if isinstance(h, dict)]


# ---- module helpers ----------------------------------------------------------


def _nth_human_message(messages: list[Any], n: int) -> HumanMessage | None:
    """0-indexed: turn 0 = first user message, turn 1 = second, etc.
    Mirrors how `kb_search.format_hits` numbers turns from 0."""
    seen = 0
    for msg in messages:
        if isinstance(msg, HumanMessage):
            if seen == n:
                return msg
            seen += 1
    return None


def _attachments_of_msg(msg: HumanMessage) -> list[dict[str, Any]]:
    extras = getattr(msg, "additional_kwargs", None) or {}
    raw = extras.get("attachments") if isinstance(extras, dict) else None
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, dict)]


def _matches_attachment_category(meta: dict[str, Any], category: str) -> bool:
    """Map the alias category to a MIME-type filter. `file` is the
    permissive bucket — matches anything; `image`, `pdf`, `audio`,
    `video` are MIME-prefix gated."""
    mime = str(meta.get("mime_type", ""))
    if category == "file":
        return True
    if category == "image":
        return mime.startswith("image/")
    if category == "pdf":
        return mime == "application/pdf"
    if category == "audio":
        return mime.startswith("audio/")
    if category == "video":
        return mime.startswith("video/")
    return False


def _latest_kb_search_tool_call_id(messages: list[Any]) -> str | None:
    """Walk backwards from the end of history; return the first
    ToolMessage's `tool_call_id` where its paired AIMessage tool_call
    was named `kb_search`."""
    # Build an index of AIMessage tool_call ids → tool name so we can
    # look up by ToolMessage.tool_call_id without an O(n^2) scan.
    tool_call_names: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                cname = (
                    call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                )
                if isinstance(cid, str) and isinstance(cname, str):
                    tool_call_names[cid] = cname
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            cid = getattr(msg, "tool_call_id", None)
            if isinstance(cid, str) and tool_call_names.get(cid) == "kb_search":
                return cid
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
