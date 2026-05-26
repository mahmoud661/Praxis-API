from __future__ import annotations

from typing import Any

from ...domain.entities.agent import Agent
from ...domain.value_objects.agent_name import AgentName
from ...domain.value_objects.identifiers import AgentId, OwnerId


def row_to_agent(row: dict[str, Any]) -> Agent:
    return Agent.rehydrate(
        id=AgentId.from_str(str(row["id"])),
        owner_id=OwnerId.from_str(str(row["owner_id"])),
        name=AgentName.create(row["name"]),
        system_prompt=row["system_prompt"],
        created_at=row["created_at"],
    )
