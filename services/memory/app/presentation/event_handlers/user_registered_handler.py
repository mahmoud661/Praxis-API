from __future__ import annotations

from typing import Any

from ...application.memory_service import MemoryService


def make_user_registered_handler(service: MemoryService):
    """Factory that binds a MemoryService to the UserRegistered event handler.

    Event envelope (auth.events.v1):
        {
          "metadata": { "eventName": "UserRegistered", "aggregateId": "<userId>", ... },
          "payload":  { "userId": "<uuid>", "email": "...", "registeredAt": "<ISO>" }
        }
    """
    async def handle(event: dict[str, Any]) -> None:
        payload = event.get("payload", {})
        user_id: str = payload["userId"]
        email: str = payload["email"]
        registered_at: str = payload.get("registeredAt", "")
        await service.provision_user(
            owner_id=user_id,
            email=email,
            registered_at=registered_at,
        )

    return handle
