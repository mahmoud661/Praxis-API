from __future__ import annotations

from typing import Protocol, Sequence

from ..shared.domain_event import DomainEvent


class EventPublisher(Protocol):
    async def publish(self, topic: str, events: Sequence[DomainEvent]) -> None: pass
