"""DI token `"ICapabilitiesService"` (impl class `CapabilitiesService`)."""

from __future__ import annotations

from typing import Protocol

from ..dtos.capability_dto import CapabilitiesView


class ICapabilitiesService(Protocol):
    async def list_capabilities(self, *, user_id: str) -> CapabilitiesView:
        """Effective capability catalog for `user_id`. Composes the
        agent registry with LiteLLM model metadata + this user's
        account state. Result is suitable for caching client-side for
        the rest of the session."""
