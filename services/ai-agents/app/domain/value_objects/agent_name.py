from __future__ import annotations

from dataclasses import dataclass

from ..shared.exceptions import ValidationException


@dataclass(frozen=True, slots=True)
class AgentName:
    value: str

    @staticmethod
    def create(raw: str) -> "AgentName":
        stripped = raw.strip()
        if not (1 <= len(stripped) <= 120):
            raise ValidationException("Agent name must be 1-120 characters")
        return AgentName(stripped)
