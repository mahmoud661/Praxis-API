from __future__ import annotations

from ...domain.entities.agent import Agent
from ...domain.value_objects.identifiers import AgentId, OwnerId
from .agent_mapper import row_to_agent
from .postgres_connection import PostgresConnection


class PostgresAgentRepository:
    """asyncpg-backed implementation of the AgentRepository port."""

    def __init__(self, conn: PostgresConnection) -> None:
        self._conn = conn

    async def save(self, agent: Agent) -> None:
        async with self._conn.pool.acquire() as c:
            await c.execute(
                """
                INSERT INTO agents (id, owner_id, name, system_prompt, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    system_prompt = EXCLUDED.system_prompt
                """,
                agent.id.value,
                agent.owner_id.value,
                agent.name.value,
                agent.system_prompt,
                agent.created_at,
            )

    async def find_by_id(self, agent_id: AgentId) -> Agent | None:
        async with self._conn.pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT id, owner_id, name, system_prompt, created_at FROM agents WHERE id = $1",
                agent_id.value,
            )
            return row_to_agent(dict(row)) if row else None

    async def list_for_owner(self, owner_id: OwnerId) -> list[Agent]:
        async with self._conn.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, owner_id, name, system_prompt, created_at "
                "FROM agents WHERE owner_id = $1 ORDER BY created_at DESC",
                owner_id.value,
            )
            return [row_to_agent(dict(r)) for r in rows]
