"""
Provision routes — called by other services to register their domain
entities in the knowledge graph.

Two endpoints, one clear responsibility each:

  POST /provision        — upsert an entity node
  POST /provision/link   — create a directed relationship between two nodes

Services call these directly instead of publishing Kafka events and waiting
for the memory service to react. Memory service stays a dumb graph store.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...application.memory_service import MemoryService

router = APIRouter(prefix="/provision", tags=["provision"])


# ── Request models ────────────────────────────────────────────────────────────

class ProvisionNodeIn(BaseModel):
    type: Literal["user", "conversation", "attachment", "image", "custom"]
    id: str                    # stable UUID — becomes the entity's uuid in Neo4j
    name: str                  # display name shown in the graph
    owner_id: str              # which user's graph this belongs to
    summary: str = ""
    metadata: dict[str, Any] = {}


class ProvisionLinkIn(BaseModel):
    from_id: str               # source entity uuid
    to_id: str                 # target entity uuid
    owner_id: str
    relationship: str          # e.g. "PARTICIPATED_IN", "BELONGS_TO"


class ProvisionOut(BaseModel):
    ok: bool


# ── Type → Neo4j label mapping ────────────────────────────────────────────────

_LABEL: dict[str, str] = {
    "user":         "Person",
    "conversation": "Conversation",
    "attachment":   "Attachment",
    "image":        "Image",
    "custom":       "Entity",
}


# ── Routes ────────────────────────────────────────────────────────────────────

def make_provision_router(service: MemoryService) -> APIRouter:

    @router.post("", response_model=ProvisionOut, status_code=200)
    async def provision_node(body: ProvisionNodeIn) -> ProvisionOut:
        """Upsert an entity node in the knowledge graph.

        Idempotent — safe to call on every create or update.
        The `id` field is used as the stable uuid; subsequent calls with the
        same id update the name and summary without creating duplicates.
        """
        label = _LABEL.get(body.type, "Entity")
        if body.type == "custom" and body.metadata.get("label"):
            label = str(body.metadata["label"])

        try:
            await service.provision_entity(
                owner_id=body.owner_id,
                entity_id=body.id,
                entity_type=label,
                name=body.name,
                summary=body.summary,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ProvisionOut(ok=True)

    @router.post("/link", response_model=ProvisionOut, status_code=200)
    async def provision_link(body: ProvisionLinkIn) -> ProvisionOut:
        """Create a directed relationship between two existing entity nodes.

        Idempotent — MERGE semantics, no duplicate edges.
        Both nodes must exist before linking (call POST /provision first).
        """
        relationship = body.relationship.upper().replace(" ", "_")
        try:
            await service.link_entities(
                owner_id=body.owner_id,
                from_entity_id=body.from_id,
                to_entity_id=body.to_id,
                relationship=relationship,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ProvisionOut(ok=True)

    return router
