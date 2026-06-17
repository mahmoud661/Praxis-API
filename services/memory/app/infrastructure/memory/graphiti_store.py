"""
GraphitiMemoryStore — IMemoryStore adapter backed by Graphiti (Neo4j graph
+ vector search). All episodes are namespaced by `owner_id` via Graphiti's
`group_id` so search results never cross user boundaries.

Graphiti is an LLM-guided knowledge-graph library: when you add an episode
it runs entity/relation extraction and writes nodes+edges to Neo4j, then
embeds the episode for hybrid retrieval.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.llm_client.openai_client import LLMConfig, OpenAIClient
from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType

from ...domain.ports.memory_store import (
    Episode,
    GraphEdge,
    GraphNode,
    IMemoryStore,
    KnowledgeGraph,
    MemoryEntity,
    MemorySearchHit,
)


class GraphitiMemoryStore:
    """Implements IMemoryStore using graphiti-core + Neo4j."""

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        llm_api_key: str,
        llm_model: str,
        llm_base_url: str | None = None,
        embedding_model: str = "text-embedding-3-small",
        embedding_api_key: str | None = None,
        embedding_base_url: str | None = None,
    ) -> None:
        llm_client = OpenAIClient(
            config=LLMConfig(
                api_key=llm_api_key,
                model=llm_model,
                base_url=llm_base_url,
            )
        )
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=embedding_api_key or llm_api_key,
                embedding_model=embedding_model,
                base_url=embedding_base_url or llm_base_url,
            )
        )
        cross_encoder = OpenAIRerankerClient(
            config=LLMConfig(
                api_key=llm_api_key,
                model=llm_model,
                base_url=llm_base_url,
            )
        )
        self._graphiti = Graphiti(
            uri=neo4j_uri,
            user=neo4j_user,
            password=neo4j_password,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )

    async def init(self) -> None:
        await self._graphiti.build_indices_and_constraints()

    async def add_episode(self, episode: Episode) -> str:
        episode_id = episode.id or str(uuid.uuid4())
        await self._graphiti.add_episode(
            name=episode_id,
            episode_body=episode.content,
            source=EpisodeType.text,
            source_description=episode.source,
            reference_time=episode.created_at or datetime.now(tz=timezone.utc),
            group_id=episode.owner_id,
        )
        return episode_id

    async def search(
        self, *, owner_id: str, query: str, k: int = 10
    ) -> list[MemorySearchHit]:
        if not query.strip():
            # Blank query: list recent episodic nodes directly from Neo4j
            # without going through the LLM search path.
            return await self._list_recent_episodes(owner_id=owner_id, k=k)

        results = await self._graphiti.search(
            query=query,
            group_ids=[owner_id],
            num_results=k,
        )
        hits: list[MemorySearchHit] = []
        for r in results:
            entities = [
                getattr(n, "name", str(n))
                for n in getattr(r, "relevant_schema", {}).get("nodes", [])
            ]
            hits.append(
                MemorySearchHit(
                    episode_id=getattr(r, "uuid", ""),
                    excerpt=getattr(r, "fact", getattr(r, "episode_body", "")),
                    score=float(getattr(r, "score", 0.0)),
                    source=getattr(r, "source_description", ""),
                    entities=entities,
                )
            )
        return hits

    async def _list_recent_episodes(
        self, *, owner_id: str, k: int
    ) -> list[MemorySearchHit]:
        """Direct Neo4j query for recent episodes — bypasses LLM search."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (e:EpisodicNode)
                WHERE e.group_id = $group_id
                RETURN e.uuid AS episode_id,
                       e.content AS content,
                       e.source_description AS source
                ORDER BY e.created_at DESC
                LIMIT $k
                """,
                group_id=owner_id,
                k=k,
            )
            records = await result.data()
        return [
            MemorySearchHit(
                episode_id=rec.get("episode_id", ""),
                excerpt=(rec.get("content") or "")[:400],
                score=1.0,
                source=rec.get("source") or "",
            )
            for rec in records
        ]

    async def list_entities(
        self, *, owner_id: str, limit: int = 50
    ) -> list[MemoryEntity]:
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n:Entity)
                WHERE n.group_id = $group_id
                RETURN n.name AS name, labels(n) AS labels, n.summary AS summary
                LIMIT $limit
                """,
                group_id=owner_id,
                limit=limit,
            )
            records = await result.data()
        entities: list[MemoryEntity] = []
        for rec in records:
            type_labels = [l for l in (rec.get("labels") or []) if l != "Entity"]
            entities.append(
                MemoryEntity(
                    name=rec.get("name", ""),
                    type=type_labels[0] if type_labels else "Entity",
                    summary=rec.get("summary", ""),
                )
            )
        return entities

    async def get_graph(
        self, *, owner_id: str, limit: int = 100
    ) -> KnowledgeGraph:
        """Return entity nodes and their Neo4j relationships for graph rendering."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            # Fetch all entity nodes for this owner
            node_result = await session.run(
                """
                MATCH (n:Entity)
                WHERE n.group_id = $group_id
                RETURN toString(id(n)) AS id,
                       n.name AS name,
                       labels(n) AS labels,
                       n.summary AS summary
                LIMIT $limit
                """,
                group_id=owner_id,
                limit=limit,
            )
            node_records = await node_result.data()

            # Fetch relationships between entities belonging to this owner
            edge_result = await session.run(
                """
                MATCH (a:Entity {group_id: $group_id})-[r]->(b:Entity {group_id: $group_id})
                RETURN toString(id(a)) AS src_id,
                       toString(id(b)) AS tgt_id,
                       type(r) AS rel_type
                LIMIT $limit
                """,
                group_id=owner_id,
                limit=limit,
            )
            edge_records = await edge_result.data()

        nodes: list[GraphNode] = []
        for rec in node_records:
            type_labels = [l for l in (rec.get("labels") or []) if l != "Entity"]
            nodes.append(
                GraphNode(
                    id=rec.get("id", ""),
                    name=rec.get("name", ""),
                    type=type_labels[0] if type_labels else "Entity",
                    summary=rec.get("summary", ""),
                )
            )

        edges: list[GraphEdge] = []
        for rec in edge_records:
            edges.append(
                GraphEdge(
                    source=rec.get("src_id", ""),
                    target=rec.get("tgt_id", ""),
                    label=rec.get("rel_type", "").replace("_", " ").title(),
                )
            )

        return KnowledgeGraph(nodes=nodes, edges=edges)

    async def provision_user(
        self, *, owner_id: str, email: str, registered_at: str
    ) -> None:
        """MERGE a Person entity node for the newly registered user.

        Merges on uuid (Graphiti's indexed + unique field) so concurrent
        calls from Kafka consumer and lazy HTTP provisioning are serialized
        by the constraint — no duplicate nodes possible.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                """
                MERGE (u:Entity {uuid: $uuid})
                ON CREATE SET
                    u.name      = $name,
                    u.group_id  = $group_id,
                    u.summary   = $summary,
                    u.created_at = $created_at
                ON MATCH SET u.name = $name
                WITH u SET u:Person
                """,
                uuid=owner_id,
                name=email,
                group_id=owner_id,
                summary="Praxis user",
                created_at=registered_at,
            )

    async def provision_entity(
        self,
        *,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        name: str,
        summary: str = "",
        created_at: str = "",
    ) -> None:
        """MERGE an entity node of the given type.

        entity_type comes from trusted code (provisioner constants), not user
        input — backtick-quoting in the Cypher makes the label safe even if
        the string contains unusual characters.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                f"""
                MERGE (e:Entity {{uuid: $uuid}})
                ON CREATE SET
                    e.name       = $name,
                    e.group_id   = $group_id,
                    e.summary    = $summary,
                    e.created_at = $created_at
                ON MATCH SET e.name = $name
                WITH e SET e:`{entity_type}`
                """,
                uuid=entity_id,
                name=name,
                group_id=owner_id,
                summary=summary,
                created_at=created_at,
            )

    async def update_entity_name(
        self,
        *,
        owner_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                """
                MATCH (e:Entity {uuid: $uuid, group_id: $group_id})
                SET e.name = $name
                """,
                uuid=entity_id,
                group_id=owner_id,
                name=name,
            )

    async def link_entities(
        self,
        *,
        owner_id: str,
        from_entity_id: str,
        to_entity_id: str,
        relationship: str,
    ) -> None:
        """MERGE a directed relationship between two entity nodes.

        Both nodes must already exist in the same group. If either is absent
        the MATCH returns nothing and the MERGE is silently skipped.
        relationship comes from trusted code — backtick-quoted for safety.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                f"""
                MATCH (a:Entity {{uuid: $from_id, group_id: $group_id}})
                MATCH (b:Entity {{uuid: $to_id,   group_id: $group_id}})
                MERGE (a)-[:`{relationship}`]->(b)
                """,
                from_id=from_entity_id,
                to_id=to_entity_id,
                group_id=owner_id,
            )

    async def delete_by_owner(self, *, owner_id: str) -> None:
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                "MATCH (n {group_id: $group_id}) DETACH DELETE n",
                group_id=owner_id,
            )

    async def close(self) -> None:
        await self._graphiti.close()
