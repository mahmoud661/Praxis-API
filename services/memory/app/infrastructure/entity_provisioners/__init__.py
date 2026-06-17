"""Entity provisioner registry.

To support a new domain event:
  1. Create <name>_provisioner.py in this package with a class that has
     ``event_name: str`` and ``async def provision(service, owner_id,
     entity_id, payload)``.
  2. Import it here and add an instance to PROVISIONERS.

main.py iterates PROVISIONERS to auto-register each event handler.
"""
from __future__ import annotations

from .conversation_provisioner import ConversationEntityProvisioner
from .conversation_renamed_provisioner import ConversationRenamedProvisioner
from .user_provisioner import UserEntityProvisioner

PROVISIONERS = [
    UserEntityProvisioner(),
    ConversationEntityProvisioner(),
    ConversationRenamedProvisioner(),
]
