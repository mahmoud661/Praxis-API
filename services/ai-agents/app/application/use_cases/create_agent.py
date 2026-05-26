from __future__ import annotations

from ...domain.entities.agent import Agent
from ...domain.ports.agent_repository import AgentRepository
from ...domain.ports.logger import Logger
from ...domain.shared.exceptions import DomainException
from ...domain.shared.result import Result
from ...domain.value_objects.agent_name import AgentName
from ...domain.value_objects.identifiers import OwnerId
from ..dtos import AgentView, CreateAgentInput


def _to_view(agent: Agent) -> AgentView:
    return AgentView(
        id=agent.id.value,
        owner_id=agent.owner_id.value,
        name=agent.name.value,
        system_prompt=agent.system_prompt,
        created_at=agent.created_at.isoformat(),
    )


class CreateAgentUseCase:
    """
    Single responsibility: validate input, build the Agent aggregate, persist.
    Dependencies are injected as ports — no asyncpg / FastAPI imports here.
    """

    def __init__(self, agents: AgentRepository, logger: Logger) -> None:
        self._agents = agents
        self._logger = logger

    async def execute(self, input: CreateAgentInput) -> Result[AgentView, DomainException]:
        try:
            owner = OwnerId.from_str(input.owner_id)
            name = AgentName.create(input.name)
            agent = Agent.create(owner_id=owner, name=name, system_prompt=input.system_prompt)
            await self._agents.save(agent)
            self._logger.info("agent.created", agent_id=agent.id.value, owner_id=owner.value)
            return Result.ok(_to_view(agent))
        except DomainException as err:
            return Result.fail(err)
