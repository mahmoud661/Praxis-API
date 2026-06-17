import pytest

from app.application.memory_service import MemoryService
from app.domain.ports.memory_store import GraphEdge, GraphNode, KnowledgeGraph, MemorySearchHit
from tests.helpers.fakes import FakeMemoryStore, SilentLogger


@pytest.fixture
def store() -> FakeMemoryStore:
    return FakeMemoryStore()


@pytest.fixture
def service(store: FakeMemoryStore) -> MemoryService:
    return MemoryService(store=store, logger=SilentLogger())


async def test_add_episode_persists(store: FakeMemoryStore, service: MemoryService) -> None:
    episode_id = await service.add_episode(owner_id="u1", content="hello world")
    assert episode_id != ""
    assert len(store._episodes) == 1
    assert store._episodes[0]["content"] == "hello world"
    assert store._episodes[0]["owner_id"] == "u1"


async def test_add_episode_skips_blank(store: FakeMemoryStore, service: MemoryService) -> None:
    result = await service.add_episode(owner_id="u1", content="   ")
    assert result == ""
    assert len(store._episodes) == 0


async def test_search_returns_hits(store: FakeMemoryStore, service: MemoryService) -> None:
    store.inject_hit(
        MemorySearchHit(episode_id="e1", excerpt="hello", score=0.9, source="conversation")
    )
    hits = await service.search(owner_id="u1", query="hello")
    assert len(hits) == 1
    assert hits[0].excerpt == "hello"


async def test_search_empty_query_returns_nothing(service: MemoryService) -> None:
    hits = await service.search(owner_id="u1", query="  ")
    assert hits == []


async def test_list_memories_returns_all_hits(
    store: FakeMemoryStore, service: MemoryService
) -> None:
    store.inject_hit(
        MemorySearchHit(episode_id="e1", excerpt="recent memory", score=1.0, source="conversation")
    )
    hits = await service.list_memories(owner_id="u1")
    assert len(hits) == 1
    assert hits[0].excerpt == "recent memory"


async def test_get_graph_returns_graph(
    store: FakeMemoryStore, service: MemoryService
) -> None:
    store.inject_graph(
        KnowledgeGraph(
            nodes=[GraphNode(id="n1", name="Alice", type="Person")],
            edges=[GraphEdge(source="n1", target="n1", label="KNOWS")],
        )
    )
    graph = await service.get_graph(owner_id="u1")
    assert len(graph.nodes) == 1
    assert graph.nodes[0].name == "Alice"
    assert len(graph.edges) == 1


async def test_provision_user_creates_entity(
    store: FakeMemoryStore, service: MemoryService
) -> None:
    await service.provision_user(
        owner_id="u1", email="alice@example.com", registered_at="2025-01-01T00:00:00Z"
    )
    assert len(store._provisioned_users) == 1
    assert store._provisioned_users[0]["owner_id"] == "u1"
    assert store._provisioned_users[0]["email"] == "alice@example.com"


async def test_delete_memories_removes_episodes(
    store: FakeMemoryStore, service: MemoryService
) -> None:
    await service.add_episode(owner_id="u1", content="keep me not")
    await service.add_episode(owner_id="u2", content="keep me")
    await service.delete_memories(owner_id="u1")
    remaining = [e for e in store._episodes if e["owner_id"] == "u1"]
    assert remaining == []
    other = [e for e in store._episodes if e["owner_id"] == "u2"]
    assert len(other) == 1
