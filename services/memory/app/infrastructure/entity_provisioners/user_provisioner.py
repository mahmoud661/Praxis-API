from __future__ import annotations

from typing import Any


class UserEntityProvisioner:
    """Handles UserRegistered → creates a Person entity node.

    Event payload (auth.events.v1 / UserRegistered):
        { "userId": "<uuid>", "email": "...", "registeredAt": "<ISO>" }
    metadata.aggregateId == userId
    """

    event_name: str = "UserRegistered"

    async def provision(
        self,
        service: Any,
        owner_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        email: str = payload["email"]
        registered_at: str = payload.get("registeredAt", "")
        await service.provision_entity(
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type="Person",
            name=email,
            summary="Praxis user",
            created_at=registered_at,
        )
