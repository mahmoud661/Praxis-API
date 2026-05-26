from __future__ import annotations

from ...domain.ports.agent_repository import AgentRepository
from ...domain.shared.exceptions import DomainException
from ...domain.shared.result import Result
from ...domain.value_objects.identifiers import OwnerId
from ..dtos import AgentView
from .create_agent import _to_view


class ListUserAgentsUseCase:
    def __init__(self, agents: AgentRepository) -> None:
        self._agents = agents

    async def execute(self, owner_id_raw: str) -> Result[list[AgentView], DomainException]:
        try:
            owner = OwnerId.from_str(owner_id_raw)
            agents = await self._agents.list_for_owner(owner)
            return Result.ok([_to_view(a) for a in agents])
        except DomainException as err:
            return Result.fail(err)
