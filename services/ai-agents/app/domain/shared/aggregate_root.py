from __future__ import annotations

from .domain_event import DomainEvent


class AggregateRoot:
    """Records domain events. The use case pulls and publishes them after
    the transaction commits."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def _add_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        out, self._events = self._events, []
        return out
