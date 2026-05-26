from __future__ import annotations

from typing import Protocol

from ..entities.agent import Agent
from ..value_objects.identifiers import AgentId, OwnerId


class AgentRepository(Protocol):
    """Persistence port. Implementations live in infrastructure."""

    async def save(self, agent: Agent) -> None: ...
    async def find_by_id(self, agent_id: AgentId) -> Agent | None: ...
    async def list_for_owner(self, owner_id: OwnerId) -> list[Agent]: ...
