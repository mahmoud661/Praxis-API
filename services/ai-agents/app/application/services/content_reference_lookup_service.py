"""
ContentReferenceLookupService — the app's implementation of the
react_agent library's `ReferenceLookup` port (registered under the DI
token `"IContentReferenceLookup"`).

Resolves model-emitted aliases (`turn3image1`, `citeturn0search2`)
back to durable entities by walking the thread's LangGraph state:

  - `turn{N}{file|image|pdf|...}{M}` →
      `HumanMessage[#N].additional_kwargs.attachments[M-of-category]`
  - `turn{N}{search|news}{M}` →
      structured `kb_search` hits the tool stashed under the
      per-thread sequence key (see `agentic/kb_citation_store.py`) —
      `turn{N}` here IS the kb_search call's sequence number.

Ownership enforced — cross-user lookups return `None` so the resolver
silently drops the alias and the frontend renders it as plain text.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from .agentic.agent_registry import AgentRegistry
from .agentic.kb_citation_store import KB_HITS_NAMESPACE, kb_hits_key
from ...domain.dtos.content_reference_dto import (
    AttachmentRef,
    WebpageRef,
    category_for_mime,
)
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore


# ---- service -----------------------------------------------------------------


class ContentReferenceLookupService:
    """Auto-bound to the DI token `"IContentReferenceLookup"`."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        agentic_store: AgenticStore,
        logger: Logger,
    ) -> None:
        self._registry = agent_registry
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
        del owner_id, category
        # `turn_index` IS the kb_search call's per-thread sequence
        # number — kb_search mints `citeturn{seq}search{n}` and stashes
        # that call's hits under the matching `seq` key. So a citation
        # resolves against the EXACT call that produced it, even when
        # the model ran several kb_search calls in one reply (they no
        # longer all collide on `turn0`).
        hits = await self._load_kb_hits(thread_id=thread_id, seq=turn_index)
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
            graph = self._registry.default_agent().get()
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
        self, *, thread_id: str, seq: int
    ) -> list[dict[str, Any]]:
        item = await self._agentic.store.aget(
            KB_HITS_NAMESPACE, kb_hits_key(thread_id, seq)
        )
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
    permissive bucket — matches anything; the specific categories are
    gated via the SAME `category_for_mime` the preload middleware mints
    from, so a minted alias always resolves under the rule that minted
    it (no mint/resolve drift)."""
    if category == "file":
        return True
    return category_for_mime(str(meta.get("mime_type", ""))) == category


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
