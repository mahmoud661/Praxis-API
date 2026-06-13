from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, Sequence

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventConsumer(Protocol):
    """Inbound side of messaging. Use cases never touch it directly —
    a dispatcher in the presentation layer wires events to handlers."""

    def on(self, event_name: str, handler: EventHandler) -> "EventConsumer": pass
    async def start(self, topics: Sequence[str]) -> None: pass
    async def stop(self) -> None: pass
