from __future__ import annotations

from typing import Any, ClassVar, Protocol


class IEntityProvisioner(Protocol):
    """One provisioner per domain event type that creates graph entities.

    To add support for a new event:
      1. Create a new class implementing this protocol in
         infrastructure/entity_provisioners/<name>_provisioner.py.
      2. Add an instance to PROVISIONERS in
         infrastructure/entity_provisioners/__init__.py.

    The generic handler in presentation/event_handlers/provision_handler.py
    reads event_name and calls provision() automatically — no other wiring.
    """

    event_name: ClassVar[str]

    async def provision(
        self,
        service: Any,  # MemoryService — Any avoids domain→application import
        owner_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None: ...
