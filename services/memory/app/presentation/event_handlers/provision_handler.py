from __future__ import annotations

from typing import Any

from ...application.memory_service import MemoryService
from ...domain.ports.entity_provisioner import IEntityProvisioner


def make_provisioner_handler(service: MemoryService, provisioner: IEntityProvisioner):
    """Generic Kafka event handler for any IEntityProvisioner.

    Extracts owner_id from payload.userId and entity_id from
    metadata.aggregateId, then delegates to the provisioner.
    """
    async def handle(event: dict[str, Any]) -> None:
        payload = event.get("payload", {})
        metadata = event.get("metadata", {})
        owner_id: str = payload["userId"]
        entity_id: str = metadata["aggregateId"]
        await provisioner.provision(service, owner_id, entity_id, payload)

    return handle
