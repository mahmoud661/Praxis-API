"""
CapabilitiesService — assembles the response for `GET /v1/capabilities`.

Composes two sources:

  1. AgentRegistry — the canonical agent catalog (each agent's `spec`).
  2. LiteLLMClient — per-model metadata (vendor, context_window,
     per-token cost) for the `underlying` block.

Auto-registered by the container as `"ICapabilitiesService"` (the
"I" + ClassName convention; see `container.py`).
"""

from __future__ import annotations

from ...domain.dtos.capability_dto import (
    AcceptsView,
    AgentPricing,
    AgentView,
    CapabilitiesView,
    LimitsView,
    Modality,
    UnderlyingView,
)
from ...domain.ports.logger import Logger
from ...infrastructure.llm.litellm_client import LiteLLMClient, ModelInfo
from .agentic.agent_registry import AgentRegistry
from .agentic.agent_spec import AgentSpec

# Bumped when the wire shape gains a non-additive change. Additive
# fields (new keys on `agents[]`, new `supports.*`, etc.) DON'T bump
# this — the frontend silently ignores unknowns. Renames or removals
# DO bump it.
_SCHEMA_VERSION = "2026-06-02"

# Default attachment caps when an agent doesn't override. 32 MiB is well
# under provider limits (Anthropic 32MiB inline, Gemini 50MiB) so a
# single source of truth here is safe across upstreams.
_DEFAULT_MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024
_DEFAULT_MAX_ATTACHMENTS_PER_TURN = 8

# MIME type allowlist per modality. The platform's authoritative
# mapping — keeps the frontend and the upload validator in sync without
# either side declaring its own list.
_MIME_TYPES_BY_MODALITY: dict[Modality, list[str]] = {
    "text": [
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    ],
    "image": [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    ],
    "pdf": ["application/pdf"],
    "audio": [
        "audio/mpeg",
        "audio/wav",
        "audio/webm",
    ],
    "video": [
        "video/mp4",
        "video/webm",
    ],
}


class CapabilitiesService:
    """Container construct: `(AgentRegistry, LiteLLMClient, Logger)`.

    All three are registered tokens; the auto-DI picks them by
    annotation class name.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        litellm_client: LiteLLMClient,
        logger: Logger,
    ) -> None:
        self._registry = agent_registry
        self._litellm = litellm_client
        self._logger = logger

    async def list_capabilities(self, *, user_id: str) -> CapabilitiesView:
        # `user_id` reserved for future per-user filtering (tier-based
        # model allowlists via LiteLLM virtual keys). Not read today.
        del user_id

        models = await self._litellm.list_models()
        specs = self._registry.specs()
        agents = [
            _spec_to_view(spec, models.get(spec.underlying_model))
            for spec in specs
        ]
        return CapabilitiesView(
            schema_version=_SCHEMA_VERSION,
            agents=agents,
            default_agent_id=self._registry.default_id(),
        )


def _spec_to_view(spec: AgentSpec, model: ModelInfo | None) -> AgentView:
    accepted_mimes = _mimes_for(spec.accepts_modalities)
    return AgentView(
        id=spec.id,
        display_name=spec.display_name,
        description=spec.description,
        icon=spec.icon,
        visibility=spec.visibility,
        accepts=AcceptsView(
            modalities=list(spec.accepts_modalities),
            mime_types=accepted_mimes,
        ),
        limits=LimitsView(
            max_attachment_bytes=(
                0 if not accepted_mimes else _DEFAULT_MAX_ATTACHMENT_BYTES
            ),
            max_attachments_per_turn=(
                0 if not accepted_mimes else _DEFAULT_MAX_ATTACHMENTS_PER_TURN
            ),
        ),
        tools=list(spec.tools),
        constraints=spec.constraints,
        underlying=_underlying_for(spec.underlying_model, model),
        deprecated_at=None,
    )


def _mimes_for(modalities: list[Modality]) -> list[str]:
    """Flatten the per-modality allowlist into one list. Text is the
    BASELINE — every agent accepts plain text inputs from the textarea,
    but the file-picker only opens for non-text modalities, so the
    derived list excludes text MIME types unless the agent has only
    text declared (in which case the list stays empty → no file picker)."""
    if list(modalities) == ["text"]:
        return []  # text-only agents don't expose a file picker
    out: list[str] = []
    for m in modalities:
        if m == "text":
            # Include text upload formats when ANY non-text modality is
            # also present — lets users drag a .md file into a vision
            # agent's composer without surprise rejection.
            out.extend(_MIME_TYPES_BY_MODALITY["text"])
        else:
            out.extend(_MIME_TYPES_BY_MODALITY.get(m, []))
    # Preserve order but de-dup (text appears before non-text by
    # convention so the picker shows familiar formats first).
    seen: set[str] = set()
    deduped: list[str] = []
    for mime in out:
        if mime not in seen:
            seen.add(mime)
            deduped.append(mime)
    return deduped


def _underlying_for(model_name: str, model: ModelInfo | None) -> UnderlyingView:
    if model is None:
        # The registry's boot-time validation should have caught this,
        # but stay graceful at request time: return a minimal block
        # rather than 500-ing on a transient LiteLLM blip.
        return UnderlyingView(
            model_id=model_name,
            vendor="unknown",
            context_window=0,
            pricing=None,
        )
    return UnderlyingView(
        model_id=model.model_name,
        vendor=model.provider,
        context_window=model.max_input_tokens,
        pricing=AgentPricing(
            input_per_1m_usd=round(model.input_cost_per_token * 1_000_000, 4),
            output_per_1m_usd=round(model.output_cost_per_token * 1_000_000, 4),
        ),
    )
