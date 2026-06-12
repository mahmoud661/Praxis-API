"""
`kb_search` — LangChain `@tool` for retrieving relevant chunks from
the user's knowledge base. General-agent specific: it's wired to this
app's `IKnowledgeService` (Qdrant) and the agentic k/v store, so it
lives in the agent's own tools folder, not in the react_agent library.

Returns a model-readable summary of the top-k hits with
`citeturn{seq}search{n}` citation aliases at the end of each chunk —
`seq` is this call's per-thread sequence number so citations from
different kb_search calls never collide. The content-reference
middleware picks the aliases up post-emission and the frontend renders
them as citation pills.

`owner_id` comes from the LangChain `RunnableConfig` so search results
can never cross users. `KnowledgeService.search` enforces the same
filter at the vector-store layer; this is defense in depth.

Constructed via `make_kb_search_tool(...)`. Capturing the services in
the tool's closure avoids any DI plumbing leaking into the agent's
tool list.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from .......domain.IServices.i_knowledge_service import IKnowledgeService
from ....kb_citation_store import (
    KB_HITS_NAMESPACE,
    kb_count_key,
    kb_hits_key,
)

if TYPE_CHECKING:
    # Annotation-only: AgenticStore pulls langgraph.checkpoint.postgres,
    # which isn't installed in every dev env. The factory isn't
    # DI-constructed, so a lazy annotation costs nothing.
    from .......infrastructure.agentic.agentic_store import AgenticStore

# ---- module constants --------------------------------------------------------

_DEFAULT_K = 5
# Per-hit text is trimmed to keep one search call from dumping a
# whole document into the prompt — embeddings retrieve at chunk
# granularity but a long chunk can still be ~1500 chars.
_HIT_PREVIEW_CHARS = 1200


# ---- factory -----------------------------------------------------------------


def make_kb_search_tool(
    *,
    knowledge_service: IKnowledgeService,
    agentic_store: "AgenticStore",
) -> BaseTool:
    """Build the tool with `knowledge_service` + `agentic_store`
    captured in its closure. `agentic_store` persists each call's
    structured hits (title, url, attribution, snippet) under the
    per-thread sequence key so `ContentReferenceLookupService` can
    resolve `citeturn{seq}search{n}` aliases without re-parsing the
    formatted text the model sees."""

    @tool
    async def kb_search(
        query: Annotated[
            str, "Natural-language question. Will be embedded as one vector."
        ],
        config: RunnableConfig,
    ) -> str:
        """Search the user's uploaded documents for passages relevant
        to a question. Call this when the user asks about topics that
        might be covered in files they've uploaded. Returns the most
        relevant excerpts with citation aliases the platform expands
        into clickable source references in the user's UI.
        """
        owner_id = _owner_id_from_config(config)
        if owner_id is None:
            return (
                "[tool error] missing owner_id on the run config — "
                "cannot scope the search to the right user."
            )
        cleaned = query.strip()
        if not cleaned:
            return "[tool error] empty query."
        hits = await knowledge_service.search(
            owner_id=owner_id, query=cleaned, k=_DEFAULT_K
        )
        if not hits:
            return (
                "[tool note] no documents matched. The user may not "
                "have uploaded any related material yet."
            )
        thread_id = _thread_id_from_config(config) or "unknown"
        # Claim this call's sequence number BEFORE formatting so the
        # alias the model copies (`citeturn{seq}search{n}`) matches the
        # store key the lookup will read.
        seq = await _next_search_seq(agentic_store, thread_id)
        await _stash_hits_for_lookup(
            agentic_store=agentic_store,
            thread_id=thread_id,
            seq=seq,
            hits=hits,
        )
        return _format_hits(hits, seq)

    return kb_search


async def _next_search_seq(agentic_store: "AgenticStore", thread_id: str) -> int:
    """Read-and-increment the per-thread kb_search call counter,
    returning the sequence number to use for THIS call. Defensive: a
    missing counter (fresh thread) or store hiccup yields seq 0 —
    which keeps the common single-call case at the familiar
    `citeturn0search{n}`."""
    key = kb_count_key(thread_id)
    current = 0
    try:
        item = await agentic_store.store.aget(KB_HITS_NAMESPACE, key)
        value = getattr(item, "value", None)
        if isinstance(value, dict) and isinstance(value.get("count"), int):
            current = value["count"]
    except Exception:  # noqa: BLE001
        # Fresh thread, missing key, or a store without aget — start at 0.
        current = 0
    try:
        await agentic_store.store.aput(
            KB_HITS_NAMESPACE, key, {"count": current + 1}
        )
    except Exception:  # noqa: BLE001
        # If the counter can't persist, the next call collides on this
        # seq — degraded, not crashing. Sequential per-thread runs make
        # this vanishingly rare.
        pass
    return current


async def _stash_hits_for_lookup(
    *,
    agentic_store: "AgenticStore",
    thread_id: str,
    seq: int,
    hits,
) -> None:
    """Persist this call's hits to the agentic k/v store under the
    per-thread search sequence number so `ContentReferenceLookupService`
    can read them later without parsing the model-facing formatted
    string."""
    payload = [
        {
            "title": _safe_str(h.chunk.extra.get("filename", h.chunk.file_id)),
            "url": _file_content_url(h.chunk.file_id),
            "attribution": _safe_str(h.chunk.extra.get("filename", "")),
            "snippet": h.chunk.text[:_HIT_PREVIEW_CHARS],
        }
        for h in hits
    ]
    # Serialize defensively — the store can carry dicts but a JSON
    # round-trip catches any accidental non-serializable field early.
    json.dumps(payload)  # raises if not serializable
    await agentic_store.store.aput(
        KB_HITS_NAMESPACE, kb_hits_key(thread_id, seq), {"hits": payload}
    )


def _safe_str(value) -> str:
    return str(value) if value is not None else ""


def _file_content_url(file_id: str) -> str:
    """Same path the frontend uses for image thumbnails — a download
    URL the frontend can click through to inspect the source chunk's
    file in full."""
    return f"/v1/files/{file_id}/content"


def _thread_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) else None


# ---- module helpers ----------------------------------------------------------


def _format_hits(hits, seq: int) -> str:
    """Render hits as a numbered list with a citation alias on each.
    The alias format matches what the content-reference scanner
    recognises (`turn{seq}search{n}`) — `seq` is THIS kb_search call's
    per-thread sequence number, so citations from different calls don't
    collide. Items are 1-indexed to match the ChatGPT convention (also
    what `read_attachment("turnNimageM")` accepts)."""
    parts: list[str] = []
    for idx, hit in enumerate(hits):
        body = hit.chunk.text
        if len(body) > _HIT_PREVIEW_CHARS:
            body = body[:_HIT_PREVIEW_CHARS] + "..."
        filename = hit.chunk.extra.get("filename", hit.chunk.file_id)
        parts.append(
            f"[{idx + 1}] {filename}\n{body}\nciteturn{seq}search{idx + 1}"
        )
    return "\n\n".join(parts)


def _owner_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    owner_id = configurable.get("owner_id")
    return owner_id if isinstance(owner_id, str) else None
