from __future__ import annotations

from typing import Any


class ConversationDeletedProvisioner:
    """Handles ConversationDeleted → soft-deletes the Conversation entity node.

    Event payload (agents.events.v1 / ConversationDeleted):
        {
          "userId":    "<owner_uuid>",
          "threadId":  "<thread_uuid>",
          "deletedAt": "<ISO>"
        }
    metadata.aggregateId == threadId
    """

    event_name: str = "ConversationDeleted"

    async def provision(
        self,
        service: Any,
        owner_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        deleted_at: str = payload.get("deletedAt", "")
        await service.soft_delete_entity(
            owner_id=owner_id,
            entity_id=entity_id,
            deleted_at=deleted_at,
        )
