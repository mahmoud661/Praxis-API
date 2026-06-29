"""
REST routes for the knowledge page frontend.

All routes read X-User-Id forwarded by the gateway after session auth —
the memory service never issues or validates sessions itself.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query

from ...application.memory_service import MemoryService
from ..schemas import (
    ContextSummaryOut,
    EntityOut,
    EpisodeExportOut,
    EpisodeIn,
    EpisodeOut,
    EpisodeStatusOut,
    ExportOut,
    ForgetIn,
    ForgetOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphTripleOut,
    GraphTriplesOut,
    KnowledgeGraphOut,
    MemoryEpisodeOut,
    SearchResultsOut,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Process-lifetime set of already-provisioned user IDs.
# provision_user is idempotent so the worst case on restart is one extra MERGE.
_provisioned: set[str] = set()

_SOURCE_BY_TYPE: dict[str, str] = {
    "episodic": "conversation",
    "semantic": "fact",
}


def _require_user(x_user_id: str | None) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    return x_user_id


async def _ensure_user_entity(
    service: MemoryService,
    owner_id: str,
    email: str | None,
) -> None:
    """Lazy-provision the Person entity for this user on first visit only."""
    if not email or owner_id in _provisioned:
        return
    await service.provision_user(owner_id=owner_id, email=email, registered_at="")
    _provisioned.add(owner_id)


def make_knowledge_router(service: MemoryService) -> APIRouter:
    @router.get("/memories", response_model=list[MemoryEpisodeOut])
    async def list_memories(
        k: int = Query(default=20, ge=1, le=100),
        x_user_id: str | None = Header(default=None),
    ) -> list[MemoryEpisodeOut]:
        """Recent memory episodes for the authenticated user."""
        owner_id = _require_user(x_user_id)
        hits = await service.list_memories(owner_id=owner_id, k=k)
        return [
            MemoryEpisodeOut(
                episode_id=h.episode_id,
                excerpt=h.excerpt,
                score=h.score,
                source=h.source,
                entities=h.entities,
                thread_name=h.thread_name,
                tags=h.tags,
            )
            for h in hits
        ]

    @router.get("/search", response_model=SearchResultsOut)
    async def search_knowledge(
        q: str = Query(min_length=1),
        k: int = Query(default=10, ge=1, le=50),
        source: str | None = Query(default=None, description="Filter by source_description ('fact' or 'conversation')"),
        x_user_id: str | None = Header(default=None),
    ) -> SearchResultsOut:
        """Hybrid graph+vector search across the user's knowledge base."""
        owner_id = _require_user(x_user_id)
        hits = await service.search(owner_id=owner_id, query=q, k=k, source_filter=source)
        return SearchResultsOut(
            hits=[
                MemoryEpisodeOut(
                    episode_id=h.episode_id,
                    excerpt=h.excerpt,
                    score=h.score,
                    source=h.source,
                    entities=h.entities,
                    thread_name=h.thread_name,
                    tags=h.tags,
                )
                for h in hits
            ]
        )

    @router.get("/entities", response_model=list[EntityOut])
    async def list_entities(
        limit: int = Query(default=50, ge=1, le=200),
        x_user_id: str | None = Header(default=None),
    ) -> list[EntityOut]:
        """Entities extracted from the user's knowledge graph."""
        owner_id = _require_user(x_user_id)
        entities = await service.list_entities(owner_id=owner_id, limit=limit)
        return [EntityOut(name=e.name, type=e.type, summary=e.summary) for e in entities]

    @router.get("/graph", response_model=KnowledgeGraphOut)
    async def get_graph(
        limit: int = Query(default=100, ge=1, le=500),
        x_user_id: str | None = Header(default=None),
        x_user_email: str | None = Header(default=None),
    ) -> KnowledgeGraphOut:
        """Entity nodes and their relationships for the knowledge graph view."""
        owner_id = _require_user(x_user_id)
        await _ensure_user_entity(service, owner_id, x_user_email)
        graph = await service.get_graph(owner_id=owner_id, limit=limit)
        return KnowledgeGraphOut(
            nodes=[
                GraphNodeOut(id=n.id, name=n.name, type=n.type, summary=n.summary, uuid=n.uuid, deleted_at=n.deleted_at)
                for n in graph.nodes
            ],
            edges=[
                GraphEdgeOut(source=e.source, target=e.target, label=e.label)
                for e in graph.edges
            ],
        )

    @router.post("/episodes", response_model=EpisodeOut, status_code=201)
    async def store_episode(
        body: EpisodeIn,
        background_tasks: BackgroundTasks,
        x_user_id: str | None = Header(default=None),
    ) -> EpisodeOut:
        """Persist a memory episode — returns immediately, extracts async.

        Graphiti's LLM entity extraction runs in the background so the
        caller (agent) is not blocked for the 30-60 s extraction window.
        The episode_id is pre-assigned and stable; the episode appears in
        search once extraction completes.
        """
        owner_id = _require_user(x_user_id)
        episode_id = str(uuid.uuid4())
        background_tasks.add_task(
            service.add_episode,
            owner_id=owner_id,
            content=body.content,
            source=_SOURCE_BY_TYPE[body.memory_type],
            thread_id=body.thread_id,
            episode_id=episode_id,
            tags=body.tags or [],
        )
        return EpisodeOut(episode_id=episode_id)

    @router.get("/episodes/{episode_id}/status", response_model=EpisodeStatusOut)
    async def get_episode_status(
        episode_id: str,
        x_user_id: str | None = Header(default=None),
    ) -> EpisodeStatusOut:
        """Return whether a queued episode has finished background extraction.

        Extracted is True once raw_content is stamped on the Episodic node.
        Callers may poll this after receiving a 201 from POST /episodes.
        """
        owner_id = _require_user(x_user_id)
        extracted = await service.get_episode_status(
            owner_id=owner_id, episode_id=episode_id
        )
        return EpisodeStatusOut(episode_id=episode_id, extracted=extracted)

    @router.delete("/episodes/{episode_id}", status_code=204)
    async def delete_episode(
        episode_id: str,
        x_user_id: str | None = Header(default=None),
    ) -> None:
        """Delete a specific episode by id. Returns 204 if deleted, 404 if not found."""
        owner_id = _require_user(x_user_id)
        found = await service.delete_episode(owner_id=owner_id, episode_id=episode_id)
        if not found:
            raise HTTPException(status_code=404, detail="Episode not found.")

    @router.get("/memories/export", response_model=ExportOut)
    async def export_memories(
        tag: str | None = Query(default=None, description="Filter by tag"),
        x_user_id: str | None = Header(default=None),
    ) -> ExportOut:
        """Export all memory episodes. Optional ?tag= filter."""
        owner_id = _require_user(x_user_id)
        episodes = await service.export_memories(owner_id=owner_id, tag=tag)
        return ExportOut(
            episodes=[EpisodeExportOut(**ep) for ep in episodes],
            total=len(episodes),
        )

    @router.get("/summary", response_model=ContextSummaryOut)
    async def get_context_summary(
        x_user_id: str | None = Header(default=None),
    ) -> ContextSummaryOut:
        """Compact context string about the user for agent injection."""
        owner_id = _require_user(x_user_id)
        context = await service.get_context_summary(owner_id=owner_id)
        return ContextSummaryOut(context=context)

    @router.get("/graph/context", response_model=GraphTriplesOut)
    async def get_graph_context(
        entity: str = Query(min_length=1, description="Entity name to look up"),
        k: int = Query(default=10, ge=1, le=50),
        x_user_id: str | None = Header(default=None),
    ) -> GraphTriplesOut:
        """Return RELATES_TO triples for entities matching the given name."""
        owner_id = _require_user(x_user_id)
        triples = await service.get_entity_triples(
            owner_id=owner_id, entity_name=entity, k=k
        )
        return GraphTriplesOut(triples=[GraphTripleOut(**t) for t in triples])

    @router.delete("/memories", status_code=204)
    async def delete_memories(
        x_user_id: str | None = Header(default=None),
    ) -> None:
        """Wipe all memory episodes for the authenticated user."""
        owner_id = _require_user(x_user_id)
        await service.delete_memories(owner_id=owner_id)

    @router.post("/memories/forget", response_model=ForgetOut)
    async def forget_memories(
        body: ForgetIn,
        x_user_id: str | None = Header(default=None),
    ) -> ForgetOut:
        """Search for episodes matching a query and delete them."""
        owner_id = _require_user(x_user_id)
        deleted = await service.forget(owner_id=owner_id, query=body.query)
        return ForgetOut(deleted=deleted)

    return router
