"""
Long-term memory tools for the general agent.

Six tools, one responsibility each:
  - memory_search       : retrieve relevant episodes/facts from Graphiti
  - memory_store        : persist something worth remembering across sessions
  - memory_forget       : delete specific memories the user wants removed
  - memory_graph_search : query structured relationship triples from the graph
  - memory_list         : list recent memories without a search query
  - memory_update       : correct or replace an existing memory by episode_id

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

from .......domain.memory_settings import MEMORY_LIST_MAX_K, MEMORY_SEARCH_K


def make_memory_search_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_search` tool with `memory_client` in its closure."""

    @tool
    async def memory_search(
        query: Annotated[str, "Natural-language question to search long-term memory."],
        memory_type: Annotated[
            Literal["all", "semantic", "episodic"],
            (
                "'all' searches everything (default). "
                "'semantic' restricts to stored facts and preferences. "
                "'episodic' restricts to past events and interactions."
            ),
        ] = "all",
        config: RunnableConfig = None,
    ) -> str:
        """Search the user's long-term memory and Graphiti knowledge graph.

        Use this when the user references a past conversation ("do you remember
        when…"), asks about something from a previous session, or when background
        context would meaningfully improve your answer.

        Tip: use memory_type='semantic' when looking up preferences or facts,
        and memory_type='episodic' when looking up past events or decisions.

        Returns ranked excerpts together with the entities Graphiti extracted.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot scope the search."
        cleaned = query.strip()
        if not cleaned:
            return "[tool error] empty query."
        try:
            hits = await memory_client.search(
                owner_id=owner_id, query=cleaned, k=MEMORY_SEARCH_K,
                memory_type=memory_type,
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] memory search failed: {exc}"
        if not hits:
            return "[tool note] no relevant memories found for this query."
        parts: list[str] = []
        for i, h in enumerate(hits, 1):
            entities = ", ".join(h.entities) if h.entities else "—"
            thread = f" [from: {h.thread_name}]" if h.thread_name else ""
            parts.append(
                f"[{i}] score={h.score:.2f} source={h.source}{thread}\n"
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
        tags: Annotated[
            list[str],
            (
                "Optional labels for this memory (e.g. ['work', 'preference', 'goal']). "
                "Tags help filter and organise memories. Pass [] if none apply."
            ),
        ] = [],
        config: RunnableConfig = None,
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
                tags=tags or [],
            )
        except Exception as exc:  # noqa: BLE001
            detail = str(exc) or type(exc).__name__
            return f"[tool error] memory store failed: {detail}"
        return f"Queued for memory extraction. episode_id={episode_id}"

    return memory_store


def make_memory_graph_search_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_graph_search` tool with `memory_client` in its closure."""

    @tool
    async def memory_graph_search(
        entity: Annotated[
            str,
            "Entity name or topic to look up (e.g. 'my job', 'Sarah', 'Optimum Partners').",
        ],
        config: RunnableConfig,
    ) -> str:
        """Look up structured relationship facts from the user's knowledge graph.

        Returns entity relationship triples extracted by Graphiti from past
        conversations — e.g. "Mahmoud works at Optimum Partners" or
        "Praxis uses Neo4j". Use this instead of memory_search when you need
        structured facts about connections between people, places, or concepts,
        rather than episode excerpts.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot scope graph search."
        cleaned = entity.strip()
        if not cleaned:
            return "[tool error] empty entity name."
        try:
            triples = await memory_client.graph_search(
                owner_id=owner_id, entity=cleaned, k=MEMORY_SEARCH_K
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] memory graph search failed: {exc}"
        if not triples:
            return f"[tool note] no graph relationships found for '{cleaned}'."
        lines = [
            f"• {t.subject} → {t.predicate} → {t.object}: {t.fact}"
            for t in triples
        ]
        return "\n".join(lines)

    return memory_graph_search


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


def make_memory_list_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_list` tool with `memory_client` in its closure."""

    @tool
    async def memory_list(
        k: Annotated[int, "Number of recent memories to return (1-20)."] = MEMORY_SEARCH_K,
        config: RunnableConfig = None,
    ) -> str:
        """List the user's most recent memories without a search query.

        Use this when the user asks 'what do you know about me?', 'show me
        your memories', or 'what have I told you before?'. Returns memories
        sorted by recency. For topic-specific recall, use memory_search instead.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot list memories."
        k = max(1, min(k, MEMORY_LIST_MAX_K))
        try:
            hits = await memory_client.search(
                owner_id=owner_id, query="", k=k, memory_type="all"
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] memory list failed: {exc}"
        if not hits:
            return "[tool note] no memories stored yet."
        parts: list[str] = []
        for i, h in enumerate(hits, 1):
            tag_str = f" [tags: {', '.join(h.tags)}]" if h.tags else ""
            thread = f" [from: {h.thread_name}]" if h.thread_name else ""
            parts.append(
                f"[{i}] source={h.source}{thread}{tag_str}\n{h.excerpt}"
            )
        return "\n\n".join(parts)

    return memory_list


def make_memory_update_tool(*, memory_client: "IMemoryClient") -> BaseTool:
    """Return the `memory_update` tool with `memory_client` in its closure."""

    @tool
    async def memory_update(
        episode_id: Annotated[
            str,
            "The episode_id of the memory to replace (from a previous memory_search or memory_list result).",
        ],
        new_content: Annotated[str, "The corrected or updated memory content."],
        memory_type: Annotated[
            Literal["episodic", "semantic"],
            "'episodic' for events, 'semantic' for facts or preferences.",
        ],
        config: RunnableConfig = None,
    ) -> str:
        """Replace an existing memory with corrected content.

        Use when the user says 'that memory is wrong', 'update what you know
        about X', or 'correct that'. Deletes the old episode by id and stores
        the new content. Use memory_search first to find the episode_id.
        """
        owner_id = _owner_id(config)
        if owner_id is None:
            return "[tool error] missing owner_id — cannot update memory."
        cleaned = new_content.strip()
        if not cleaned:
            return "[tool error] empty new_content."
        thread_id = _thread_id(config)
        try:
            deleted = await memory_client.delete_episode(
                owner_id=owner_id, episode_id=episode_id
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] failed to delete old episode: {exc}"
        if not deleted:
            return f"[tool error] no memory found with episode_id={episode_id}."
        try:
            new_id = await memory_client.store(
                owner_id=owner_id,
                content=cleaned,
                memory_type=memory_type,
                thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            return f"[tool error] old memory deleted but re-store failed: {exc}"
        return (
            f"Memory updated. Deleted episode_id={episode_id}. "
            f"New episode_id={new_id} queued for extraction."
        )

    return memory_update


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
