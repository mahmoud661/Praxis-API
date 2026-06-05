"""
REST controller for `GET /v1/capabilities`. Pure HTTP shape — the
heavy lifting is in `ICapabilitiesService`.

The response Pydantic models are intentionally separate from the
domain DTOs (`capability_dto.py`) so future internal refactors don't
ripple to the frontend. Same convention as `threads_controller.py`.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from ...domain.dtos.capability_dto import (
    AcceptsView,
    AccountView,
    AgentView,
    CapabilitiesView,
    LimitsView,
    UnderlyingView,
)
from ...domain.IServices.i_capabilities_service import ICapabilitiesService
from ..http.dependencies import current_user_id


# ---- Response Pydantic models ---------------------------------------------


class AgentToolResponse(BaseModel):
    id: str
    label: str
    default_enabled: bool
    user_toggleable: bool


class AgentConstraintsResponse(BaseModel):
    max_runtime_seconds: int | None = None
    max_iterations: int | None = None
    streams_partial_tokens: bool = True


class AgentPricingResponse(BaseModel):
    input_per_1m_usd: float
    output_per_1m_usd: float


class AcceptsResponse(BaseModel):
    modalities: list[str]
    mime_types: list[str]


class LimitsResponse(BaseModel):
    max_attachment_bytes: int
    max_attachments_per_turn: int


class UnderlyingResponse(BaseModel):
    model_id: str
    vendor: str
    context_window: int
    pricing: AgentPricingResponse | None = None

    @classmethod
    def from_view(cls, u: UnderlyingView) -> "UnderlyingResponse":
        return cls(
            model_id=u.model_id,
            vendor=u.vendor,
            context_window=u.context_window,
            pricing=(
                AgentPricingResponse(
                    input_per_1m_usd=u.pricing.input_per_1m_usd,
                    output_per_1m_usd=u.pricing.output_per_1m_usd,
                )
                if u.pricing is not None
                else None
            ),
        )


class AgentResponse(BaseModel):
    id: str
    display_name: str
    description: str
    icon: str | None = None
    visibility: Literal["public", "beta", "internal"]
    accepts: AcceptsResponse
    limits: LimitsResponse
    tools: list[AgentToolResponse] = Field(default_factory=list)
    constraints: AgentConstraintsResponse | None = None
    underlying: UnderlyingResponse
    deprecated_at: str | None = None

    @classmethod
    def from_view(cls, a: AgentView) -> "AgentResponse":
        return cls(
            id=a.id,
            display_name=a.display_name,
            description=a.description,
            icon=a.icon,
            visibility=a.visibility,
            accepts=_accepts(a.accepts),
            limits=_limits(a.limits),
            tools=[
                AgentToolResponse(
                    id=t.id,
                    label=t.label,
                    default_enabled=t.default_enabled,
                    user_toggleable=t.user_toggleable,
                )
                for t in a.tools
            ],
            constraints=(
                AgentConstraintsResponse(
                    max_runtime_seconds=a.constraints.max_runtime_seconds,
                    max_iterations=a.constraints.max_iterations,
                    streams_partial_tokens=a.constraints.streams_partial_tokens,
                )
                if a.constraints is not None
                else None
            ),
            underlying=UnderlyingResponse.from_view(a.underlying),
            deprecated_at=a.deprecated_at,
        )


class AccountResponse(BaseModel):
    tier: str
    monthly_message_quota: int
    messages_remaining: int
    feature_flags: list[str] = Field(default_factory=list)

    @classmethod
    def from_view(cls, a: AccountView) -> "AccountResponse":
        return cls(
            tier=a.tier,
            monthly_message_quota=a.monthly_message_quota,
            messages_remaining=a.messages_remaining,
            feature_flags=list(a.feature_flags),
        )


class CapabilitiesResponse(BaseModel):
    schema_version: str
    agents: list[AgentResponse]
    default_agent_id: str
    account: AccountResponse

    @classmethod
    def from_view(cls, v: CapabilitiesView) -> "CapabilitiesResponse":
        return cls(
            schema_version=v.schema_version,
            agents=[AgentResponse.from_view(a) for a in v.agents],
            default_agent_id=v.default_agent_id,
            account=AccountResponse.from_view(v.account),
        )


# ---- Controller -----------------------------------------------------------


def _accepts(a: AcceptsView) -> AcceptsResponse:
    return AcceptsResponse(modalities=list(a.modalities), mime_types=list(a.mime_types))


def _limits(lim: LimitsView) -> LimitsResponse:
    return LimitsResponse(
        max_attachment_bytes=lim.max_attachment_bytes,
        max_attachments_per_turn=lim.max_attachments_per_turn,
    )


class CapabilitiesController:
    """Container resolves `service: ICapabilitiesService` from the token."""

    def __init__(self, service: ICapabilitiesService) -> None:
        self._service = service

    async def get_capabilities(
        self, user_id: str = Depends(current_user_id)
    ) -> CapabilitiesResponse:
        view = await self._service.list_capabilities(user_id=user_id)
        return CapabilitiesResponse.from_view(view)
