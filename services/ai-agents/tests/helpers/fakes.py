"""
In-memory test doubles for the domain ports. Same idea as the TS side:
because the application layer talks only to interfaces, use cases run in
isolation — no Postgres, no Kafka in unit tests.
"""

from __future__ import annotations

from typing import Any

from app.domain.entities.agent import Agent
from app.domain.value_objects.identifiers import AgentId, OwnerId


class InMemoryAgentRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Agent] = {}

    async def save(self, agent: Agent) -> None:
        self._by_id[agent.id.value] = agent

    async def find_by_id(self, agent_id: AgentId) -> Agent | None:
        return self._by_id.get(agent_id.value)

    async def list_for_owner(self, owner_id: OwnerId) -> list[Agent]:
        return [a for a in self._by_id.values() if a.owner_id == owner_id]

    # Helpers used only by tests.
    @property
    def all(self) -> list[Agent]:
        return list(self._by_id.values())


class SilentLogger:
    def debug(self, msg: str, **ctx: Any) -> None: ...
    def info(self, msg: str, **ctx: Any) -> None: ...
    def warning(self, msg: str, **ctx: Any) -> None: ...
    def error(self, msg: str, **ctx: Any) -> None: ...
