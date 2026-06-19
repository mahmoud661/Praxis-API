"""
MCP server for the memory service.

Exposes two tools:
  - memory_search  : hybrid Graphiti graph+vector search over the user's memory
  - memory_store   : persist a new episode (episodic or semantic)

Both tools receive `owner_id` explicitly so the server is stateless and
multi-tenant — the caller injects it from the session context.
"""
from typing import Literal

from mcp.server.fastmcp import FastMCP

from ..application.memory_service import MemoryService

_SOURCE_BY_TYPE: dict[str, str] = {
    "episodic": "conversation",
    "semantic": "fact",
}


def make_mcp_server(service: MemoryService) -> FastMCP:
    mcp = FastMCP("memory-mcp")

    @mcp.tool()
    async def memory_search(
        query: str,
        owner_id: str,
        k: int = 10,
    ) -> list[dict]:
        """Search long-term episodic memory and the Graphiti knowledge graph.

        Returns a ranked list of memory hits, each with an excerpt of the
        remembered content, the entities Graphiti extracted, a relevance
        score, and the source type (conversation / fact / document).
        """
        hits = await service.search(owner_id=owner_id, query=query, k=k)
        return [
            {
                "episode_id": h.episode_id,
                "excerpt": h.excerpt,
                "score": h.score,
                "source": h.source,
                "entities": h.entities,
            }
            for h in hits
        ]

    @mcp.tool()
    async def memory_store(
        content: str,
        owner_id: str,
        memory_type: Literal["episodic", "semantic"] = "episodic",
    ) -> str:
        """Persist a new memory episode for the user.

        memory_type:
          "episodic"  — an event or interaction that happened.
          "semantic"  — a durable fact or preference about the user.

        Returns the assigned episode id.
        """
        source = _SOURCE_BY_TYPE.get(memory_type, "conversation")
        return await service.add_episode(
            owner_id=owner_id, content=content, source=source
        )

    return mcp
