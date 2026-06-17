from __future__ import annotations

from typing import Any


class ConversationEntityProvisioner:
    """Handles ConversationCreated → creates a Conversation entity and
    links it to the owner's Person entity.

    Event payload (agents.events.v1 / ConversationCreated):
        {
          "userId":    "<owner_uuid>",
          "threadId":  "<thread_uuid>",
          "title":     "New conversation",
          "createdAt": "<ISO>"
        }
    metadata.aggregateId == threadId
    """

    event_name: str = "ConversationCreated"

    async def provision(
        self,
        service: Any,
        owner_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        title: str = payload.get("title", "Conversation")
        created_at: str = payload.get("createdAt", "")

        await service.provision_entity(
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type="Conversation",
            name=title,
            summary="AI conversation thread",
            created_at=created_at,
        )
        # Link Person → Conversation so the graph shows the user's threads.
        # owner_id doubles as the Person entity's uuid (set in UserEntityProvisioner).
        await service.link_entities(
            owner_id=owner_id,
            from_entity_id=owner_id,
            to_entity_id=entity_id,
            relationship="PARTICIPATED_IN",
        )
