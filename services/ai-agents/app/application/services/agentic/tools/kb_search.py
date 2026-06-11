"""
`kb_search` — LangChain `@tool` for retrieving relevant chunks from
the user's knowledge base.

Returns a model-readable summary of the top-k hits with `[turn0search{n}]`
citation aliases at the end of each chunk. The content-reference
middleware picks those aliases up post-emission and the frontend
renders them as citation pills (same machinery as ChatGPT's
`citeturn0search2` pattern).

`owner_id` comes from the LangChain `RunnableConfig` so search results
can never cross users. `KnowledgeService.search` enforces the same
filter at the vector-store layer; this is defense in depth.

Constructed via `make_kb_search_tool(knowledge_service)`. Capturing
the service in the tool's closure avoids any DI plumbing leaking into
the agent's tool list.
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from .....domain.IServices.i_knowledge_service import IKnowledgeService
from .....infrastructure.agentic.agentic_store import AgenticStore


# ---- module constants --------------------------------------------------------

_DEFAULT_K = 5
# Per-hit text is trimmed to keep one search call from dumping a
# whole document into the prompt — embeddings retrieve at chunk
# granularity but a long chunk can still be ~1500 chars.
_HIT_PREVIEW_CHARS = 1200

# Namespace in the LangGraph k/v store where each kb_search call
# persists its structured hits, keyed by `(thread_id, tool_call_id)`.
# The `ContentReferenceLookupService` reads from here to resolve
# `citeturn0search{n}` aliases back to (title, url, attribution,
# snippet) without re-parsing the formatted tool-result text.
_KB_HITS_NAMESPACE = ("kb_search_hits",)


# ---- factory -----------------------------------------------------------------


def make_kb_search_tool(
    *,
    knowledge_service: IKnowledgeService,
    agentic_store: AgenticStore,
) -> BaseTool:
    """Build the tool with `knowledge_service` + `agentic_store`
    captured in its closure. Returns the BaseTool ready to bind into
    the agent's `tools=[...]`.

    `agentic_store` is used to stash structured hit metadata (title,
    url, attribution, snippet) keyed by `(thread_id, tool_call_id)`
    so the `ContentReferenceLookupService` can resolve
    `citeturn0search{n}` aliases back to webpages without re-parsing
    the formatted text the model sees.
    """

    @tool
    async def kb_search(
        query: Annotated[
            str, "Natural-language question. Will be embedded as one vector."
        ],
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
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
        await _stash_hits_for_lookup(
            agentic_store=agentic_store,
            config=config,
            tool_call_id=tool_call_id,
            hits=hits,
        )
        return _format_hits(hits)

    return kb_search


async def _stash_hits_for_lookup(
    *,
    agentic_store: AgenticStore,
    config: RunnableConfig,
    tool_call_id: str,
    hits,
) -> None:
    """Persist this call's hits to the agentic k/v store so the
    `ContentReferenceLookupService` can read them later without
    parsing the model-facing formatted string. Key is `tool_call_id`
    (globally unique within a thread) so multiple `kb_search` calls
    in the same turn don't collide."""
    payload = [
        {
            "title": _safe_str(h.chunk.extra.get("filename", h.chunk.file_id)),
            "url": _file_content_url(h.chunk.file_id),
            "attribution": _safe_str(h.chunk.extra.get("filename", "")),
            "snippet": h.chunk.text[:_HIT_PREVIEW_CHARS],
        }
        for h in hits
    ]
    thread_id = _thread_id_from_config(config) or "unknown"
    key = f"{thread_id}:{tool_call_id}"
    # Serialize defensively — the store can carry dicts but a JSON
    # round-trip catches any accidental non-serializable field early.
    json.dumps(payload)  # raises if not serializable
    await agentic_store.store.aput(_KB_HITS_NAMESPACE, key, {"hits": payload})


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


def _format_hits(hits) -> str:
    """Render hits as a numbered list with a citation alias on each.
    The alias format matches what the content-reference scanner
    recognises (`turn0search{n}`) — the frontend swaps each one for
    a citation pill linking back to the source file."""
    parts: list[str] = []
    for idx, hit in enumerate(hits):
        body = hit.chunk.text
        if len(body) > _HIT_PREVIEW_CHARS:
            body = body[:_HIT_PREVIEW_CHARS] + "..."
        filename = hit.chunk.extra.get("filename", hit.chunk.file_id)
        # `turn0search{idx+1}` is what the content-reference
        # middleware picks up. 1-indexed to match the ChatGPT
        # convention (also what `read_attachment("turnNimageM")`
        # accepts). `turn0` is a placeholder until the alias system
        # carries the actual turn index through to the tool runtime.
        parts.append(
            f"[{idx + 1}] {filename}\n{body}\nciteturn0search{idx + 1}"
        )
    return "\n\n".join(parts)


def _owner_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    owner_id = configurable.get("owner_id")
    return owner_id if isinstance(owner_id, str) else None
