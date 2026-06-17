from __future__ import annotations

from typing import Any


class ConversationRenamedProvisioner:
    """Handles ConversationRenamed → updates the Conversation entity name.

    Event payload (agents.events.v1 / ConversationRenamed):
        {
          "userId":   "<owner_uuid>",
          "threadId": "<thread_uuid>",
          "title":    "The generated title"
        }
    metadata.aggregateId == threadId
    """

    event_name: str = "ConversationRenamed"

    async def provision(
        self,
        service: Any,
        owner_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        title: str = payload["title"]
        await service.update_entity_name(
            owner_id=owner_id,
            entity_id=entity_id,
            name=title,
        )
