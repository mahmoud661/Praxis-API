from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CreateAgentInput:
    owner_id: str
    name: str
    system_prompt: str = ""


@dataclass(frozen=True, slots=True)
class AgentView:
    id: str
    owner_id: str
    name: str
    system_prompt: str
    created_at: str  # ISO-8601
