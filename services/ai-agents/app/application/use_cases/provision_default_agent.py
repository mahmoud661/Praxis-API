from __future__ import annotations

from typing import Any

from ...domain.entities.agent import Agent
from ...domain.ports.agent_repository import AgentRepository
from ...domain.ports.logger import Logger
from ...domain.value_objects.agent_name import AgentName
from ...domain.value_objects.identifiers import OwnerId


class ProvisionDefaultAgentUseCase:
    """
    Triggered by the `UserRegistered` event arriving over Kafka.

    Inbound events are *commands* to this service. The use case is the
    application-level reaction; the consumer/dispatcher is presentation.
    """

    def __init__(self, agents: AgentRepository, logger: Logger) -> None:
        self._agents = agents
        self._logger = logger

    async def execute(self, envelope: dict[str, Any]) -> None:
        payload = envelope.get("payload") or {}
        user_id = payload.get("userId")
        if not user_id:
            self._logger.warning("provision_default_agent.missing_user_id")
            return

        owner = OwnerId.from_str(user_id)
        agent = Agent.create(
            owner_id=owner,
            name=AgentName.create("Default Agent"),
            system_prompt="You are a helpful assistant.",
        )
        await self._agents.save(agent)
        self._logger.info("agent.default.provisioned", owner_id=owner.value)
