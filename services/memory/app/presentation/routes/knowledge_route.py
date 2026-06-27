"""
REST routes for the knowledge page frontend.

All routes read X-User-Id forwarded by the gateway after session auth —
the memory service never issues or validates sessions itself.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from pydantic import BaseModel

from ...application.memory_service import MemoryService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Process-lifetime set of already-provisioned user IDs.
# The first GET /graph call provisions the user in Neo4j; all subsequent
# calls skip the write entirely. Resets only on service restart, which is
# fine — provision_user is idempotent so the worst case is one extra MERGE.
_provisioned: set[str] = set()


def _require_user(x_user_id: str | None) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    return x_user_id


async def _ensure_user_entity(
    service: MemoryService,
    owner_id: str,
    email: str | None,
) -> None:
    """Lazy-provision the Person entity for this user on first visit only.
    Skipped on all subsequent requests (process-lifetime cache) and when
    email is unavailable (direct service calls / tests)."""
    if not email or owner_id in _provisioned:
        return
    await service.provision_user(owner_id=owner_id, email=email, registered_at="")
    _provisioned.add(owner_id)


# ---- response models ---------------------------------------------------------

class MemoryEpisodeOut(BaseModel):
    episode_id: str
    excerpt: str
    score: float
    source: str
    entities: list[str]
    thread_name: str = ""


class EntityOut(BaseModel):
    name: str
    type: str
    summary: str


_SOURCE_BY_TYPE: dict[str, str] = {
    "episodic": "conversation",
    "semantic": "fact",
}


class EpisodeIn(BaseModel):
    content: str
    memory_type: Literal["episodic", "semantic"] = "episodic"
    thread_id: str | None = None


class EpisodeOut(BaseModel):
    episode_id: str


class GraphNodeOut(BaseModel):
    id: str
    name: str
    type: str
    summary: str
    uuid: str = ""
    deleted_at: str | None = None


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    label: str


class KnowledgeGraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class SearchResultsOut(BaseModel):
    hits: list[MemoryEpisodeOut]


# ---- routes ------------------------------------------------------------------

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
        return [
            EntityOut(name=e.name, type=e.type, summary=e.summary)
            for e in entities
        ]

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
        )
        return EpisodeOut(episode_id=episode_id)

    class ContextSummaryOut(BaseModel):
        context: str

    class GraphTripleOut(BaseModel):
        subject: str
        predicate: str
        object: str
        fact: str

    class GraphTriplesOut(BaseModel):
        triples: list[GraphTripleOut]

    @router.get("/summary", response_model=ContextSummaryOut)
    async def get_context_summary(
        x_user_id: str | None = Header(default=None),
    ) -> ContextSummaryOut:
        """Compact context string about the user for agent injection.

        Called by the agent runner before the first turn of a new thread to
        prime the model with what it already knows about the user.
        """
        owner_id = _require_user(x_user_id)
        context = await service.get_context_summary(owner_id=owner_id)
        return ContextSummaryOut(context=context)

    @router.get("/graph/context", response_model=GraphTriplesOut)
    async def get_graph_context(
        entity: str = Query(min_length=1, description="Entity name to look up"),
        k: int = Query(default=10, ge=1, le=50),
        x_user_id: str | None = Header(default=None),
    ) -> GraphTriplesOut:
        """Return RELATES_TO triples for entities matching the given name.

        Used by the memory_graph_search agent tool to surface structured
        relationship facts rather than raw episode excerpts.
        """
        owner_id = _require_user(x_user_id)
        triples = await service.get_entity_triples(
            owner_id=owner_id, entity_name=entity, k=k
        )
        return GraphTriplesOut(
            triples=[GraphTripleOut(**t) for t in triples]
        )

    @router.delete("/memories", status_code=204)
    async def delete_memories(
        x_user_id: str | None = Header(default=None),
    ) -> None:
        """Wipe all memory episodes for the authenticated user."""
        owner_id = _require_user(x_user_id)
        await service.delete_memories(owner_id=owner_id)

    class ForgetIn(BaseModel):
        query: str

    class ForgetOut(BaseModel):
        deleted: int

    @router.post("/memories/forget", response_model=ForgetOut)
    async def forget_memories(
        body: ForgetIn,
        x_user_id: str | None = Header(default=None),
    ) -> ForgetOut:
        """Search for episodes matching a query and delete them.

        Called by the agent when the user says 'forget that X' or
        'remove the memory about Y'.
        """
        owner_id = _require_user(x_user_id)
        deleted = await service.forget(owner_id=owner_id, query=body.query)
        return ForgetOut(deleted=deleted)

    return router
