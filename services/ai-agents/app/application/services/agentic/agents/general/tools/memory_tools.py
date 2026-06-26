"""
Long-term memory tools for the general agent.

Three tools, one responsibility each:
  - memory_search  : retrieve relevant episodes/facts from Graphiti
  - memory_store   : persist something worth remembering across sessions
  - memory_forget  : delete specific memories the user wants removed

`owner_id` comes from the LangChain `RunnableConfig` — same pattern as
`kb_search` — so memory is always scoped to the right user without the
agent having to pass it explicitly.

All three are built via factory functions that capture the `IMemoryClient`
in a closure, keeping DI plumbing out of the tool list.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

if TYPE_CHECKING:
    from .......domain.ports.i_memory_client import IMemoryClient

_DEFAULT_K = 10


def make_memory_search_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_search` tool with `memory_client` in its closure."""

    @tool
    async def memory_search(
        query: Annotated[str, "Natural-language question to search long-term memory."],
        config: RunnableConfig,
    ) -> str:
        """Search the user's long-term memory and Graphiti knowledge graph.

        Use this when the user references a past conversation ("do you remember
        when…"), asks about something from a previous session, or when background
        context would meaningfully improve your answer.

        Returns ranked excerpts from the user's episodic and semantic memory,
        together with the entities Graphiti extracted from each episode.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot scope the search."
        cleaned = query.strip()
        if not cleaned:
            return "[tool error] empty query."
        try:
            hits = await memory_client.search(
                owner_id=owner_id, query=cleaned, k=_DEFAULT_K
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] memory search failed: {exc}"
        if not hits:
            return "[tool note] no relevant memories found for this query."
        parts: list[str] = []
        for i, h in enumerate(hits, 1):
            entities = ", ".join(h.entities) if h.entities else "—"
            parts.append(
                f"[{i}] score={h.score:.2f} source={h.source}\n"
                f"{h.excerpt}\n"
                f"Entities: {entities}"
            )
        return "\n\n".join(parts)

    return memory_search


def make_memory_store_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_store` tool with `memory_client` in its closure."""

    @tool
    async def memory_store(
        content: Annotated[str, "The information to remember."],
        memory_type: Annotated[
            Literal["episodic", "semantic"],
            (
                "'episodic' for events/interactions that happened "
                "(e.g. 'User told me they just started at Acme Corp'). "
                "'semantic' for durable facts or preferences "
                "(e.g. 'User prefers Python and dislikes verbose APIs')."
            ),
        ],
        config: RunnableConfig,
    ) -> str:
        """Persist a piece of information to the user's long-term memory.

        Call this proactively after learning something worth remembering across
        sessions: a preference, a key life update, a recurring topic, a decision.

        memory_type:
          "episodic"  — something that happened (event, conversation, decision).
          "semantic"  — something that is true about the user (preference, fact, skill).
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot store memory."
        cleaned = content.strip()
        if not cleaned:
            return "[tool error] empty content."
        thread_id = _thread_id(config)
        try:
            episode_id = await memory_client.store(
                owner_id=owner_id,
                content=cleaned,
                memory_type=memory_type,
                thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            detail = str(exc) or type(exc).__name__
            return f"[tool error] memory store failed: {detail}"
        return f"Stored. episode_id={episode_id}"

    return memory_store


def make_memory_forget_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_forget` tool with `memory_client` in its closure."""

    @tool
    async def memory_forget(
        query: Annotated[str, "Description of the memory to forget (e.g. 'that I work at Acme')."],
        config: RunnableConfig,
    ) -> str:
        """Delete specific memories matching a description.

        Use when the user says "forget that X", "remove the memory about Y",
        or "that's wrong, delete it". Searches for matching episodes and
        deletes them. Returns how many were removed.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot forget memory."
        cleaned = query.strip()
        if not cleaned:
            return "[tool error] empty query."
        try:
            deleted = await memory_client.forget(owner_id=owner_id, query=cleaned)
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] memory forget failed: {exc}"
        if deleted == 0:
            return "No matching memories found to delete."
        return f"Deleted {deleted} memory episode(s) matching '{cleaned}'."

    return memory_forget



def _owner_id(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    v = configurable.get("owner_id")
    return v if isinstance(v, str) else None


def _thread_id(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    v = configurable.get("thread_id")
    return v if isinstance(v, str) else None
