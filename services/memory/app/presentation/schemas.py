"""Pydantic request / response schemas for the memory service HTTP surface.

Kept separate from routes so the shapes can be imported by tests, client
generators, and other routes without pulling in FastAPI routing machinery.
"""
from typing import Literal

from pydantic import BaseModel


# ── Inbound ───────────────────────────────────────────────────────────────────

class EpisodeIn(BaseModel):
    content: str
    memory_type: Literal["episodic", "semantic"] = "episodic"
    thread_id: str | None = None
    tags: list[str] = []


class ForgetIn(BaseModel):
    query: str


# ── Outbound — episodes ───────────────────────────────────────────────────────

class EpisodeOut(BaseModel):
    episode_id: str


class EpisodeStatusOut(BaseModel):
    episode_id: str
    extracted: bool


class EpisodeExportOut(BaseModel):
    episode_id: str
    content: str
    source: str
    created_at: str
    tags: list[str]
    entities: list[str]


class ExportOut(BaseModel):
    episodes: list[EpisodeExportOut]
    total: int


# ── Outbound — memory search / list ──────────────────────────────────────────

class MemoryEpisodeOut(BaseModel):
    episode_id: str
    excerpt: str
    score: float
    source: str
    entities: list[str]
    thread_name: str = ""
    tags: list[str] = []


class SearchResultsOut(BaseModel):
    hits: list[MemoryEpisodeOut]


class ForgetOut(BaseModel):
    deleted: int


# ── Outbound — entities ───────────────────────────────────────────────────────

class EntityOut(BaseModel):
    name: str
    type: str
    summary: str


# ── Outbound — knowledge graph ────────────────────────────────────────────────

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


class GraphTripleOut(BaseModel):
    subject: str
    predicate: str
    object: str
    fact: str


class GraphTriplesOut(BaseModel):
    triples: list[GraphTripleOut]


# ── Outbound — context summary ────────────────────────────────────────────────

class ContextSummaryOut(BaseModel):
    context: str
