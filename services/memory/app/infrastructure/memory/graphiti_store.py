"""
GraphitiMemoryStore — IMemoryStore adapter backed by Graphiti (Neo4j graph
+ vector search). All episodes are namespaced by `owner_id` via Graphiti's
`group_id` so search results never cross user boundaries.

Graphiti is an LLM-guided knowledge-graph library: when you add an episode
it runs entity/relation extraction and writes nodes+edges to Neo4j, then
embeds the episode for hybrid retrieval.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.cross_encoder import OpenAIRerankerClient
from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.openai_client import LLMConfig, OpenAIClient
from graphiti_core.nodes import EpisodeType

from ...domain.entity_types import ENTITY_TYPES
from ...domain.ports.memory_store import (
    Episode,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    MemoryEntity,
    MemorySearchHit,
)
from ...domain.settings import SEMANTIC_DEDUP_THRESHOLD


def _parse_dt(value: str) -> datetime:
    """Parse ISO-8601 string to timezone-aware datetime, defaulting to now."""
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(tz=timezone.utc)


def _strip_speaker_prefix(text: str) -> str:
    """Remove the 'Name: ' speaker prefix added before ingestion.

    Only used as a fallback for legacy nodes that pre-date the raw_content field.
    """
    if ": " in text:
        return text.split(": ", 1)[1]
    return text


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

        # SHA-256 exact dedup — skip extraction if this owner already has identical content.
        content_hash = hashlib.sha256(
            f"{episode.owner_id}:{episode.content}".encode()
        ).hexdigest()
        driver = self._graphiti.driver
        async with driver.session() as session:
            dup = await session.run(
                "MATCH (ep:Episodic {content_hash: $h, group_id: $g}) "
                "RETURN ep.name AS name LIMIT 1",
                h=content_hash,
                g=episode.owner_id,
            )
            dup_record = await dup.single()
            if dup_record:
                return dup_record["name"]

        # Semantic dedup: for fact episodes, skip re-extraction if a very
        # similar episode already exists. Catches near-duplicates SHA-256 misses.
        if episode.source == "fact":
            similar = await self._graphiti.search(
                query=episode.content[:500],
                group_ids=[episode.owner_id],
                num_results=1,
            )
            if similar:
                top_score = float(getattr(similar[0], "score", 0.0))
                if top_score >= SEMANTIC_DEDUP_THRESHOLD:
                    existing_id = (
                        getattr(similar[0], "name", "")
                        or getattr(similar[0], "uuid", "")
                    )
                    if existing_id:
                        return existing_id

        user_name = await self._get_entity_name(episode.owner_id)
        # Speaker prefix ties extracted entities back to the provisioned user node.
        formatted_body = f"{user_name}: {episode.content}" if user_name else episode.content
        await self._graphiti.add_episode(
            name=episode_id,
            episode_body=formatted_body,
            source=EpisodeType.message,
            source_description=episode.source,
            reference_time=episode.created_at or datetime.now(tz=timezone.utc),
            group_id=episode.owner_id,
            entity_types=ENTITY_TYPES,
        )
        # Stamp dedup hash, original content (raw_content), and caller tags on the node.
        async with driver.session() as session:
            await session.run(
                "MATCH (ep:Episodic {name: $name, group_id: $g}) "
                "SET ep.content_hash = $h, ep.raw_content = $raw, ep.tags = $tags",
                name=episode_id,
                g=episode.owner_id,
                h=content_hash,
                raw=episode.content,
                tags=episode.tags or [],
            )
        # If the user entity still has an email name, try to promote it to a
        # real name from the episode content. Only when email (contains @) so
        # named users are never overwritten by a later episode's name-claim.
        promoted = False
        if user_name and "@" in user_name:
            promoted = await self._maybe_promote_user_name(
                owner_id=episode.owner_id,
                current_name=user_name,
                content=episode.content,
            )
        # Merge duplicates only after a promotion — that's the only moment a
        # second node with the same name can appear from extraction.
        if promoted:
            await self._merge_duplicate_user_nodes(owner_id=episode.owner_id)
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
        current_name: str,
        content: str,
    ) -> bool:
        """Detect a name-claim in content and update the user entity node.

        Returns True if the name was changed so the caller knows to merge
        duplicate nodes created by Graphiti's concurrent extraction.
        """
        # Match "my name is X" / "call me X"; name limited to 1–3 words.
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
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                WITH root, dup
                MATCH (dup)-[r:RELATES_TO]->(other:Entity)
                WHERE other <> root
                MERGE (root)-[:RELATES_TO {
                  name: r.name, fact: r.fact, uuid: r.uuid,
                  group_id: r.group_id, created_at: r.created_at
                }]->(other)
                DELETE r
                """,
                owner_id=owner_id,
            )
            await session.run(
                """
                MATCH (root:Entity {uuid: $owner_id, group_id: $owner_id})
                MATCH (dup:Entity {name: root.name, group_id: $owner_id})
                WHERE dup.uuid <> root.uuid
                WITH root, dup
                MATCH (other:Entity)-[r:RELATES_TO]->(dup)
                WHERE other <> root
                MERGE (other)-[:RELATES_TO {
                  name: r.name, fact: r.fact, uuid: r.uuid,
                  group_id: r.group_id, created_at: r.created_at
                }]->(root)
                DELETE r
                """,
                owner_id=owner_id,
            )
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
        self, *, owner_id: str, query: str, k: int = 10, source_filter: str | None = None
    ) -> list[MemorySearchHit]:
        if not query.strip():
            # Blank query: list recent episodic nodes directly from Neo4j
            # without going through the LLM search path.
            return await self._list_recent_episodes(
                owner_id=owner_id, k=k, source_filter=source_filter
            )

        # Over-fetch when filtering so we still return k results after pruning.
        fetch_k = k * 2 if source_filter else k
        results = await self._graphiti.search(
            query=query,
            group_ids=[owner_id],
            num_results=fetch_k,
        )
        hits: list[MemorySearchHit] = []
        episode_ids = []
        for r in results:
            source_desc = getattr(r, "source_description", "")
            if source_filter and source_desc != source_filter:
                continue
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
            # Placeholder excerpt — overwritten by _enrich_hits() below with
            # the stamped raw_content so the exact agent input is preserved.
            raw_fallback = getattr(r, "fact", getattr(r, "episode_body", ""))
            hits.append(
                MemorySearchHit(
                    episode_id=episode_id,
                    excerpt=raw_fallback,
                    score=float(getattr(r, "score", 0.0)),
                    source=source_desc,
                    entities=entities,
                )
            )
            if len(hits) >= k:
                break
        # Single-query enrichment: swap in raw_content (original agent input)
        # and attach thread_name — one round-trip instead of two.
        if hits:
            enriched = await self._enrich_hits(owner_id=owner_id, episode_ids=episode_ids)
            for hit in hits:
                meta = enriched.get(hit.episode_id, {})
                # raw_content wins; fall back to stripping the prefix from
                # the Graphiti-stored body for nodes written before this field.
                hit.excerpt = meta.get("raw_content") or _strip_speaker_prefix(hit.excerpt)
                hit.thread_name = meta.get("thread_name") or ""
                hit.tags = meta.get("tags") or []
        return hits

    async def _enrich_hits(
        self, *, owner_id: str, episode_ids: list[str]
    ) -> dict[str, dict]:
        """Single Neo4j round-trip returning raw_content, tags, and thread_name per episode."""
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
                       ep.raw_content AS raw_content,
                       ep.tags AS tags,
                       collect(DISTINCT conv.name)[0] AS thread_name
                """,
                ids=episode_ids,
                owner_id=owner_id,
            )
            records = await result.data()
        return {
            rec["episode_id"]: {
                "raw_content": rec.get("raw_content") or "",
                "thread_name": rec.get("thread_name") or "",
                "tags": rec.get("tags") or [],
            }
            for rec in records
        }

    async def _list_recent_episodes(
        self, *, owner_id: str, k: int, source_filter: str | None = None
    ) -> list[MemorySearchHit]:
        """Direct Neo4j query for recent episodes — bypasses LLM search."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            src_clause = "AND e.source_description = $src" if source_filter else ""
            result = await session.run(
                f"""
                MATCH (e:Episodic {{group_id: $group_id}})
                WHERE true {src_clause}
                OPTIONAL MATCH (e)-[:MENTIONS]->(entity:Entity {{group_id: $group_id}})
                  -[:DISCUSSED_IN]->(conv:Entity)
                WITH e, collect(DISTINCT conv.name)[0] AS thread_name
                RETURN e.name AS episode_id,
                       e.raw_content AS raw_content,
                       e.content AS content,
                       e.source_description AS source,
                       e.tags AS tags,
                       thread_name
                ORDER BY e.created_at DESC
                LIMIT $k
                """,
                group_id=owner_id,
                k=k,
                src=source_filter or "",
            )
            records = await result.data()
        results = []
        for rec in records:
            # Prefer stamped raw_content (original agent input); fall back to
            # prefix-stripping for nodes written before raw_content was added.
            raw = rec.get("raw_content") or _strip_speaker_prefix(
                (rec.get("content") or "")[:400]
            )
            results.append(
                MemorySearchHit(
                    episode_id=rec.get("episode_id", ""),
                    excerpt=raw[:400],
                    score=1.0,
                    source=rec.get("source") or "",
                    thread_name=rec.get("thread_name") or "",
                    tags=rec.get("tags") or [],
                )
            )
        return results

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
            type_labels = [lbl for lbl in (rec.get("labels") or []) if lbl != "Entity"]
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
            type_labels = [lbl for lbl in (rec.get("labels") or []) if lbl != "Entity"]
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

    async def get_summary(self, *, owner_id: str) -> dict:
        """Return a compact summary of what the graph knows about this user.

        Used by the agent runner to inject a one-time context message at the
        start of each new thread so the agent is not cold.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            entity_result = await session.run(
                """
                MATCH (n:Entity {group_id: $g})
                WHERE (n.provisioned IS NULL OR n.provisioned = false)
                  AND n.deleted_at IS NULL
                WITH n, size([(n)-[]-() | 1]) AS degree
                WHERE degree > 0
                ORDER BY degree DESC
                LIMIT 8
                RETURN n.name AS name, labels(n) AS labels, n.summary AS summary
                """,
                g=owner_id,
            )
            entity_records = await entity_result.data()

            thread_result = await session.run(
                """
                MATCH (c:Entity:Conversation {group_id: $g})
                ORDER BY c.created_at DESC
                LIMIT 4
                RETURN c.name AS name
                """,
                g=owner_id,
            )
            thread_records = await thread_result.data()

            fact_result = await session.run(
                """
                MATCH (e:Episodic {group_id: $g, source_description: "fact"})
                ORDER BY e.created_at DESC
                LIMIT 6
                RETURN coalesce(e.raw_content, e.content) AS content
                """,
                g=owner_id,
            )
            fact_records = await fact_result.data()

        entities = [
            {"name": r["name"], "labels": [lbl for lbl in (r["labels"] or []) if lbl != "Entity"]}
            for r in entity_records
        ]
        threads = [r["name"] for r in thread_records if r.get("name")]
        facts = [(r.get("content") or "")[:300] for r in fact_records if r.get("content")]

        return {"entities": entities, "threads": threads, "facts": facts}

    async def get_entity_triples(
        self, *, owner_id: str, entity_name: str, k: int = 10
    ) -> list[dict]:
        """Return RELATES_TO triples for entities whose name contains entity_name.

        Each triple is {subject, predicate, object, fact} and represents a
        relationship extracted by Graphiti from the user's episodes.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n:Entity {group_id: $g})
                WHERE toLower(n.name) CONTAINS toLower($q)
                   OR toLower($q) CONTAINS toLower(n.name)
                WITH n LIMIT 3
                MATCH (a:Entity {group_id: $g})-[r:RELATES_TO]->(b:Entity {group_id: $g})
                WHERE (a = n OR b = n) AND r.fact IS NOT NULL AND r.fact <> ""
                RETURN a.name AS subject,
                       coalesce(r.name, type(r)) AS predicate,
                       b.name AS object,
                       r.fact AS fact
                LIMIT $k
                """,
                g=owner_id,
                q=entity_name,
                k=k,
            )
            records = await result.data()
        return [
            {
                "subject": r["subject"],
                "predicate": (r["predicate"] or "").replace("_", " ").lower(),
                "object": r["object"],
                "fact": r["fact"],
            }
            for r in records
        ]

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
            # Collect entity node IDs that would become orphaned BEFORE deleting
            # the episodes — once edges are severed the query can't find them.
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

            if orphan_ids:
                await session.run(
                    "UNWIND $ids AS nid MATCH (n) WHERE id(n) = nid DETACH DELETE n",
                    ids=orphan_ids,
                )

            return deleted

    async def delete_episode(self, *, owner_id: str, episode_id: str) -> bool:
        """Delete a single episode by id. Returns True if found and deleted."""
        deleted = await self.delete_episodes(
            owner_id=owner_id, episode_ids=[episode_id]
        )
        return deleted > 0

    async def get_episode_status(self, *, owner_id: str, episode_id: str) -> bool:
        """Return True if the episode's raw_content has been stamped (extraction done)."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (ep:Episodic {name: $name, group_id: $g})
                RETURN ep.raw_content IS NOT NULL AS extracted
                """,
                name=episode_id,
                g=owner_id,
            )
            record = await result.single()
        if record is None:
            return False
        return bool(record["extracted"])

    async def export_episodes(
        self, *, owner_id: str, tag: str | None = None
    ) -> list[dict]:
        """Export all episodes with their metadata. Optional tag filter."""
        driver = self._graphiti.driver
        async with driver.session() as session:
            tag_clause = "AND $tag IN ep.tags" if tag else ""
            result = await session.run(
                f"""
                MATCH (ep:Episodic {{group_id: $g}})
                WHERE true {tag_clause}
                OPTIONAL MATCH (ep)-[:MENTIONS]->(ent:Entity {{group_id: $g}})
                WITH ep, collect(DISTINCT ent.name) AS entities
                RETURN ep.name          AS episode_id,
                       coalesce(ep.raw_content, ep.content) AS content,
                       ep.source_description AS source,
                       toString(ep.created_at) AS created_at,
                       ep.tags         AS tags,
                       entities
                ORDER BY ep.created_at DESC
                """,
                g=owner_id,
                tag=tag or "",
            )
            records = await result.data()
        return [
            {
                "episode_id": r.get("episode_id", ""),
                "content": (r.get("content") or "")[:2000],
                "source": r.get("source") or "",
                "created_at": r.get("created_at") or "",
                "tags": r.get("tags") or [],
                "entities": r.get("entities") or [],
            }
            for r in records
        ]

    async def delete_by_owner(self, *, owner_id: str) -> None:
        """Delete all episodic memory for this owner without touching provisioned
        nodes (user entity, conversation/attachment nodes created by the platform).
        Provisioned nodes carry `provisioned = true`; everything else is fair game.
        """
        driver = self._graphiti.driver
        async with driver.session() as session:
            await session.run(
                "MATCH (n:Episodic {group_id: $group_id}) DETACH DELETE n",
                group_id=owner_id,
            )
            # provisioned=true nodes (user, conversation, attachment) must survive.
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
