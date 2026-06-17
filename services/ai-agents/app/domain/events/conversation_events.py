from __future__ import annotations

from ..shared.domain_event import DomainEvent


def make_conversation_renamed(
    *,
    thread_id: str,
    owner_id: str,
    title: str,
) -> DomainEvent:
    """Build a ConversationRenamed event, emitted when the auto-titler or
    a user rename gives a conversation its final name."""
    return DomainEvent(
        metadata=DomainEvent.make_metadata(
            event_name="ConversationRenamed",
            aggregate_id=thread_id,
        ),
        payload={
            "userId": owner_id,
            "threadId": thread_id,
            "title": title,
        },
    )


def make_conversation_created(
    *,
    thread_id: str,
    owner_id: str,
    title: str,
    created_at: str,
) -> DomainEvent:
    """Build a ConversationCreated domain event.

    The memory service's ConversationEntityProvisioner listens for this on
    agents.events.v1 and creates a Conversation node linked to the user.
    """
    return DomainEvent(
        metadata=DomainEvent.make_metadata(
            event_name="ConversationCreated",
            aggregate_id=thread_id,
        ),
        payload={
            "userId": owner_id,
            "threadId": thread_id,
            "title": title,
            "createdAt": created_at,
        },
    )
