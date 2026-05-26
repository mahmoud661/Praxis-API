from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainEventMetadata:
    event_id: str
    occurred_at: str
    event_name: str
    aggregate_id: str
    version: int


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """
    Base for events the domain emits. Concrete events extend this with
    a typed payload; metadata is stamped at construction.
    """

    metadata: DomainEventMetadata
    payload: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_metadata(*, event_name: str, aggregate_id: str, version: int = 1) -> DomainEventMetadata:
        return DomainEventMetadata(
            event_id=str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc).isoformat(),
            event_name=event_name,
            aggregate_id=aggregate_id,
            version=version,
        )
