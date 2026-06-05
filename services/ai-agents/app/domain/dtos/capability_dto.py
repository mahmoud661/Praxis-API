"""
View DTOs for `GET /v1/capabilities`.

The application layer talks in these dataclasses; the controller maps to
Pydantic response models at the HTTP boundary. Same convention as
`thread_dto.py` (dataclass internal / Pydantic external).

Shape mirrors the contract in the spec issue — see Praxis-API#25.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Re-export the literal types from the spec so callers can import a
# single module for the whole capability surface without reaching back
# into the application layer.
from ...application.services.agentic.agent_spec import (  # noqa: F401
    AgentConstraints,
    AgentPricing,
    AgentTool,
    Modality,
    Visibility,
)


@dataclass(frozen=True, slots=True)
class AcceptsView:
    """Input modalities + the MIME-type allowlist derived from them.

    `mime_types` is what the frontend's file picker uses for the
    `accept=` attribute on `<input type="file">` and what the drop-zone
    validates against before upload. Derived server-side so MIME-modality
    mapping lives in ONE place.
    """

    modalities: list[Modality]
    mime_types: list[str]


@dataclass(frozen=True, slots=True)
class LimitsView:
    """Per-agent attachment bounds."""

    max_attachment_bytes: int
    max_attachments_per_turn: int


@dataclass(frozen=True, slots=True)
class UnderlyingView:
    """Informational surface for the underlying LiteLLM model. The
    frontend treats this as opaque metadata — the user-facing
    abstraction is the agent. Surfaced so a future cost-aware UI has
    everything it needs without another round-trip."""

    model_id: str
    vendor: str
    context_window: int
    pricing: AgentPricing | None = None


@dataclass(frozen=True, slots=True)
class AgentView:
    """One agent's capability surface as returned by the endpoint."""

    id: str
    display_name: str
    description: str
    icon: str | None
    visibility: Visibility

    accepts: AcceptsView
    limits: LimitsView
    tools: list[AgentTool]
    constraints: AgentConstraints | None

    underlying: UnderlyingView

    deprecated_at: str | None = None


@dataclass(frozen=True, slots=True)
class AccountView:
    """Account-level state that applies across every agent."""

    tier: str
    monthly_message_quota: int
    messages_remaining: int
    feature_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CapabilitiesView:
    """Full payload for `GET /v1/capabilities`."""

    schema_version: str
    agents: list[AgentView]
    default_agent_id: str
    account: AccountView
