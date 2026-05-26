from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _validate_uuid(s: str, label: str) -> str:
    if not _UUID_RE.match(s):
        raise ValueError(f"Invalid {label}: {s}")
    return s


@dataclass(frozen=True, slots=True)
class AgentId:
    value: str

    @staticmethod
    def generate() -> "AgentId":
        return AgentId(str(uuid.uuid4()))

    @staticmethod
    def from_str(raw: str) -> "AgentId":
        return AgentId(_validate_uuid(raw, "AgentId"))


@dataclass(frozen=True, slots=True)
class OwnerId:
    """User id of the agent's owner — kept as a separate type from AgentId
    so the compiler (and reader) can't confuse the two."""

    value: str

    @staticmethod
    def from_str(raw: str) -> "OwnerId":
        return OwnerId(_validate_uuid(raw, "OwnerId"))
