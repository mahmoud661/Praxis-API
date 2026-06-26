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

from pydantic import BaseModel


class Person(BaseModel):
    """A human being — the user themselves, a colleague, friend, family member, or public figure."""


class Organization(BaseModel):
    """A company, institution, team, government body, or any named group of people."""


class Place(BaseModel):
    """A geographic location — country, city, region, landmark, or physical place."""


class Concept(BaseModel):
    """An abstract idea, technology, framework, methodology, skill, or domain of knowledge."""


class Event(BaseModel):
    """A specific occurrence that happened or is planned — a meeting, project, deadline, or milestone."""


class Preference(BaseModel):
    """A preference, taste, like, dislike, or habitual behavior of the user."""


class Fact(BaseModel):
    """A factual statement, belief, or piece of information about the user or their world."""


_ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "Person":       Person,
    "Organization": Organization,
    "Place":        Place,
    "Concept":      Concept,
    "Event":        Event,
    "Preference":   Preference,
    "Fact":         Fact,
}


def _parse_dt(value: str) -> datetime:
    """Parse ISO-8601 string to timezone-aware datetime, defaulting to now."""
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(tz=timezone.utc)

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
        llm_temperature: float = 0.0,
        llm_small_model: str | None = None,
        embedding_model: str = "text-embedding-3-small",
        embedding_api_key: str | None = None,
        embedding_base_url: str | None = None,
    ) -> None:
        llm_cfg = LLMConfig(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
            temperature=llm_temperature,
            small_model=llm_small_model,
        )
        llm_client = OpenAIClient(config=llm_cfg)
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=embedding_api_key or llm_api_key,
                embedding_model=embedding_model,
                base_url=embedding_base_url or llm_base_url,
            )
        )
        cross_encoder = OpenAIRerankerClient(config=llm_cfg)
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
        user_name = await self._get_entity_name(episode.owner_id)
        # Prefix with the user's known entity name so Graphiti's extract_message
        # prompt extracts them as the speaker and deduplicates against the
        # existing provisioned user node, linking all entities back to it.
        formatted_body = f"{user_name}: {episode.content}" if user_name else episode.content
        await self._graphiti.add_episode(
            name=episode_id,
            episode_body=formatted_body,
            source=EpisodeType.message,
            source_description=episode.source,
            reference_time=episode.created_at or datetime.now(tz=timezone.utc),
            group_id=episode.owner_id,
            entity_types=_ENTITY_TYPES,
        )
        # After extraction, promote any Person entity that was co-mentioned
        # with the user in this episode as a potential name claim. If the user
        # entity is still named by email (contains @) and the episode contains
        # a name-claim phrase, rename the user node so all future episodes use
        # the real name as the speaker prefix and dedup correctly.
        promoted = False
        if user_name and "@" in user_name:
            promoted = await self._maybe_promote_user_name(
                owner_id=episode.owner_id,
                episode_id=episode_id,
                current_name=user_name,
                content=episode.content,
            )
        # Merge duplicate user-name nodes — only needed right after a name
        # promotion, since that's the only moment a new node with the same
        # name can have been created by Graphiti's extraction.
        if promoted:
            await self._merge_duplicate_user_nodes(owner_id=episode.owner_id)
        # Link all entities extracted from this episode to the originating
        # conversation thread node via a DISCUSSED_IN edge.
        if episode.thread_id:
            await self._link_episode_to_thread(
                owner_id=episode.owner_id,
                episode_id=episode_id,
                thread_id=episode.thread_id,
            )
        return episode_id

    async def _maybe_promote_user_name(
        self,
        *,
        owner_id: str,
        episode_id: str,
        current_name: str,
        content: str,
    ) -> bool:
        """If a name-claim is detected in the content, update the user's entity
        node name to the stated name so future episodes link correctly.
        Returns True if the name was actually changed."""
        import re
        # Match "my name is X" or "call me X" — case-insensitive so
        # "my name is mahmoud" works as well as "My name is Mahmoud".
        # Name must be 1–3 words; stop words filtered after capture.
        _STOP = {"a", "an", "the", "not", "also", "just", "very", "quite"}
        pattern = re.compile(
            r"(?:my name is|call me)\s+([A-Za-z][A-Za-z'-]+(?:\s+[A-Za-z][A-Za-z'-]+){0,2})",
            re.IGNORECASE,
        )
        match = pattern.search(content)
        if not match:
            return False
        # Title-case so "mahmoud zuriqi" → "Mahmoud Zuriqi" in the graph.
        claimed_name = match.group(1).strip().rstrip(".,!?").title()
        if (not claimed_name or len(claimed_name) < 2
                or claimed_name.lower() in _STOP
                or claimed_name.lower() == current_name.lower()):
            return False
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                "MATCH (n:Entity {uuid: $uuid}) SET n.name = $name",
                uuid=owner_id,
                name=claimed_name,
            )
        return True

    async def _merge_duplicate_user_nodes(self, *, owner_id: str) -> None:
        """Collapse all entity nodes that share the user's current display name
        into the canonical user node (uuid == owner_id), transferring edges."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            # Transfer MENTIONS edges from duplicates to the canonical node
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                WITH root, dup
                MATCH (ep)-[r:MENTIONS]->(dup)
                MERGE (ep)-[:MENTIONS]->(root)
                DELETE r
                """,
                owner_id=owner_id,
            )
            # Transfer RELATES_TO edges from duplicates to the canonical node
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                WITH root, dup
                MATCH (dup)-[r:RELATES_TO]->(other:Entity)
                WHERE other <> root
                MERGE (root)-[:RELATES_TO {name: r.name, fact: r.fact, uuid: r.uuid, group_id: r.group_id, created_at: r.created_at}]->(other)
                DELETE r
                """,
                owner_id=owner_id,
            )
            # Transfer incoming RELATES_TO edges
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                WITH root, dup
                MATCH (other:Entity)-[r:RELATES_TO]->(dup)
                WHERE other <> root
                MERGE (other)-[:RELATES_TO {name: r.name, fact: r.fact, uuid: r.uuid, group_id: r.group_id, created_at: r.created_at}]->(root)
                DELETE r
                """,
                owner_id=owner_id,
            )
            # Delete the now-orphaned duplicate nodes
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                DETACH DELETE dup
                """,
                owner_id=owner_id,
            )

    async def _link_episode_to_thread(
        self, *, owner_id: str, episode_id: str, thread_id: str
    ) -> None:
        """Create DISCUSSED_IN edges from every entity mentioned in this episode
        to the Conversation node that represents the originating thread."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                """
                MATCH (ep:Episodic {name: $episode_id})
                MATCH (ep)-[:MENTIONS]->(entity:Entity {group_id: $owner_id})
                MATCH (conv:Entity {uuid: $thread_id, group_id: $owner_id})
                MERGE (entity)-[:DISCUSSED_IN]->(conv)
                """,
                episode_id=episode_id,
                owner_id=owner_id,
                thread_id=thread_id,
            )

    async def _get_entity_name(self, owner_id: str) -> str | None:
        """Return the current display name of the user's entity node, or None."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                "MATCH (n:Entity {uuid: $uuid}) RETURN n.name AS name LIMIT 1",
                uuid=owner_id,
            )
            record = await result.single()
            return record["name"] if record else None

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
        episode_ids = []
        for r in results:
            entities = [
                getattr(n, "name", str(n))
                for n in getattr(r, "relevant_schema", {}).get("nodes", [])
            ]
            # Prefer `name` over `uuid`: we store our generated episode_id as
            # the Graphiti episode `name` (add_episode(name=episode_id, ...)).
            # `r.uuid` is Graphiti's internal UUID and does NOT match the
            # `{name: eid}` clause in delete_episodes — using uuid here was
            # causing forget() to silently delete nothing.
            episode_id = getattr(r, "name", "") or getattr(r, "uuid", "")
            episode_ids.append(episode_id)
            hits.append(
                MemorySearchHit(
                    episode_id=episode_id,
                    excerpt=getattr(r, "fact", getattr(r, "episode_body", "")),
                    score=float(getattr(r, "score", 0.0)),
                    source=getattr(r, "source_description", ""),
                    entities=entities,
                )
            )
        # Enrich with thread names via DISCUSSED_IN
        if hits:
            thread_names = await self._fetch_thread_names(
                owner_id=owner_id, episode_ids=episode_ids
            )
            for hit in hits:
                hit.thread_name = thread_names.get(hit.episode_id, "")
        return hits

    async def _fetch_thread_names(
        self, *, owner_id: str, episode_ids: list[str]
    ) -> dict[str, str]:
        """Return {episode_id: conversation_name} for each episode.

        Uses path traversal (ep)-[:MENTIONS]->(entity)-[:DISCUSSED_IN]->(conv)
        instead of a cross-product filter so Neo4j uses index-backed hops.
        """
        if not episode_ids:
            return {}
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                UNWIND $ids AS eid
                MATCH (ep:Episodic {name: eid, group_id: $owner_id})
                OPTIONAL MATCH (ep)-[:MENTIONS]->(entity:Entity)
                              -[:DISCUSSED_IN]->(conv:Entity)
                RETURN eid AS episode_id,
                       collect(DISTINCT conv.name)[0] AS thread_name
                """,
                ids=episode_ids,
                owner_id=owner_id,
            )
            records = await result.data()
        return {
            rec["episode_id"]: rec.get("thread_name") or ""
            for rec in records
        }

    async def _list_recent_episodes(
        self, *, owner_id: str, k: int
    ) -> list[MemorySearchHit]:
        """Direct Neo4j query for recent episodes — bypasses LLM search."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Episodic {group_id: $group_id})
                OPTIONAL MATCH (e)-[:MENTIONS]->(entity:Entity {group_id: $group_id})
                  -[:DISCUSSED_IN]->(conv:Entity)
                WITH e, collect(DISTINCT conv.name)[0] AS thread_name
                RETURN e.name AS episode_id,
                       e.content AS content,
                       e.source_description AS source,
                       thread_name
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
                thread_name=rec.get("thread_name") or "",
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
                ORDER BY n.name ASC
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
                       n.uuid AS uuid,
                       n.name AS name,
                       labels(n) AS labels,
                       n.summary AS summary,
                       n.deleted_at AS deleted_at
                LIMIT $limit
                """,
                group_id=owner_id,
                limit=limit,
            )
            node_records = await node_result.data()

            # Fetch relationships — use a much higher cap than nodes so a
            # dense graph doesn't appear artificially disconnected.
            edge_result = await session.run(
                """
                MATCH (a:Entity {group_id: $group_id})-[r]->(b:Entity {group_id: $group_id})
                RETURN toString(id(a)) AS src_id,
                       toString(id(b)) AS tgt_id,
                       coalesce(r.name, type(r)) AS rel_type
                LIMIT $edge_limit
                """,
                group_id=owner_id,
                edge_limit=limit * 10,
            )
            edge_records = await edge_result.data()

        nodes: list[GraphNode] = []
        for rec in node_records:
            type_labels = [l for l in (rec.get("labels") or []) if l != "Entity"]
            raw_deleted = rec.get("deleted_at")
            nodes.append(
                GraphNode(
                    id=rec.get("id", ""),
                    name=rec.get("name", ""),
                    type=type_labels[0] if type_labels else "Entity",
                    summary=rec.get("summary", ""),
                    uuid=rec.get("uuid") or "",
                    deleted_at=str(raw_deleted) if raw_deleted is not None else None,
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
                    u.name        = $name,
                    u.group_id    = $group_id,
                    u.summary     = $summary,
                    u.created_at  = $created_at,
                    u.provisioned = true
                ON MATCH SET u.name = $name, u.provisioned = true
                WITH u SET u:Person
                """,
                uuid=owner_id,
                name=email,
                group_id=owner_id,
                summary="Praxis user",
                created_at=_parse_dt(registered_at),
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
                    e.name        = $name,
                    e.group_id    = $group_id,
                    e.summary     = $summary,
                    e.created_at  = $created_at,
                    e.provisioned = true
                ON MATCH SET e.name = $name, e.provisioned = true
                WITH e SET e:`{entity_type}`
                """,
                uuid=entity_id,
                name=name,
                group_id=owner_id,
                summary=summary,
                created_at=_parse_dt(created_at),
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

    async def soft_delete_entity(
        self,
        *,
        owner_id: str,
        entity_id: str,
        deleted_at: str,
    ) -> None:
        """Stamp deleted_at on the entity node — keeps it in the graph but
        marks it as disabled so the frontend can render it differently."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                """
                MATCH (e:Entity {uuid: $uuid, group_id: $group_id})
                SET e.deleted_at = $deleted_at
                """,
                uuid=entity_id,
                group_id=owner_id,
                deleted_at=deleted_at,
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

    async def delete_episodes(self, *, owner_id: str, episode_ids: list[str]) -> int:
        """Delete specific Episodic nodes and clean up entity nodes that become
        orphaned (no remaining MENTIONS from any episode, not provisioned)."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            # Step 1: collect entity node IDs mentioned only by these episodes
            # before we sever the edges, so we know what to clean up.
            orphan_result = await session.run(
                """
                UNWIND $ids AS eid
                MATCH (ep:Episodic {name: eid, group_id: $owner_id})
                      -[:MENTIONS]->(entity:Entity)
                WHERE (entity.provisioned IS NULL OR entity.provisioned = false)
                WITH entity
                WHERE NOT EXISTS {
                    MATCH (other:Episodic {group_id: $owner_id})
                          -[:MENTIONS]->(entity)
                    WHERE NOT other.name IN $ids
                }
                RETURN collect(id(entity)) AS orphan_ids
                """,
                ids=episode_ids,
                owner_id=owner_id,
            )
            orphan_record = await orphan_result.single()
            orphan_ids: list[int] = (orphan_record["orphan_ids"] if orphan_record else []) or []

            # Step 2: delete the episodes themselves.
            del_result = await session.run(
                """
                UNWIND $ids AS eid
                MATCH (ep:Episodic {name: eid, group_id: $owner_id})
                DETACH DELETE ep
                RETURN count(ep) AS deleted
                """,
                ids=episode_ids,
                owner_id=owner_id,
            )
            del_record = await del_result.single()
            deleted = del_record["deleted"] if del_record else 0

            # Step 3: delete orphaned entity nodes (no remaining episode links).
            if orphan_ids:
                await session.run(
                    "UNWIND $ids AS nid MATCH (n) WHERE id(n) = nid DETACH DELETE n",
                    ids=orphan_ids,
                )

            return deleted

    async def delete_by_owner(self, *, owner_id: str) -> None:
        """Delete all episodic memory for this owner without touching provisioned
        nodes (user entity, conversation/attachment nodes created by the platform).
        Provisioned nodes carry `provisioned = true`; everything else is fair game.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            # 1. Delete all Episodic nodes (the actual memory episodes).
            await session.run(
                "MATCH (n:Episodic {group_id: $group_id}) DETACH DELETE n",
                group_id=owner_id,
            )
            # 2. Delete Entity nodes created by Graphiti extraction (not provisioned).
            #    Provisioned nodes (user, conversation, attachment) have provisioned=true
            #    and must survive so the user's identity stays intact.
            await session.run(
                """
                MATCH (n:Entity {group_id: $group_id})
                WHERE (n.provisioned IS NULL OR n.provisioned = false)
                DETACH DELETE n
                """,
                group_id=owner_id,
            )

    async def close(self) -> None:
        await self._graphiti.close()
