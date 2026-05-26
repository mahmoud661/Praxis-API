from __future__ import annotations

from datetime import datetime, timezone

from ..shared.aggregate_root import AggregateRoot
from ..value_objects.agent_name import AgentName
from ..value_objects.identifiers import AgentId, OwnerId


class Agent(AggregateRoot):
    """
    Aggregate root.

    Constructed in two named ways:
      - `create`: brand new, would emit events here if/when needed
      - `rehydrate`: load from persistence (no events)
    """

    def __init__(
        self,
        *,
        id: AgentId,
        owner_id: OwnerId,
        name: AgentName,
        system_prompt: str,
        created_at: datetime,
    ) -> None:
        super().__init__()
        self._id = id
        self._owner_id = owner_id
        self._name = name
        self._system_prompt = system_prompt
        self._created_at = created_at

    @staticmethod
    def create(*, owner_id: OwnerId, name: AgentName, system_prompt: str = "") -> "Agent":
        return Agent(
            id=AgentId.generate(),
            owner_id=owner_id,
            name=name,
            system_prompt=system_prompt,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def rehydrate(
        *,
        id: AgentId,
        owner_id: OwnerId,
        name: AgentName,
        system_prompt: str,
        created_at: datetime,
    ) -> "Agent":
        return Agent(
            id=id,
            owner_id=owner_id,
            name=name,
            system_prompt=system_prompt,
            created_at=created_at,
        )

    @property
    def id(self) -> AgentId:
        return self._id

    @property
    def owner_id(self) -> OwnerId:
        return self._owner_id

    @property
    def name(self) -> AgentName:
        return self._name

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def created_at(self) -> datetime:
        return self._created_at
