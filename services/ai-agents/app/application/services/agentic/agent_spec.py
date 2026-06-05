"""
AgentSpec — the user-facing capability declaration for an agent.

Every `BaseAgent` subclass carries a `spec: ClassVar[AgentSpec]`. The agent
registry discovers subclasses at boot and the capabilities controller turns
each spec into a JSON view the frontend reads via `GET /v1/capabilities`.

Capability surface is an AGENT property, not a model property: the same
underlying LiteLLM model can power one agent that accepts images and
another that intentionally doesn't (e.g., a research agent that rejects
non-text input by design). The model is an implementation detail —
`underlying_model` here is just the LiteLLM `model_name` the agent's
LangGraph nodes call.

The spec must hold up to two invariants checked at registry boot:

  1. `accepts_modalities` must be a subset of the underlying model's
     declared modalities (a "vision" agent can't run on a text-only
     model). Validated by `AgentRegistry.validate_against()` using the
     LiteLLM `/model/info` response.

  2. Tool ids in `tools` must be unique within the spec. Enforced here
     via a Pydantic validator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# The set of input modalities the platform knows about. Adding a new one
# is a one-line change here plus a UI-side affordance; the registry will
# refuse to boot an agent declaring a modality not in this union.
Modality = Literal["text", "image", "pdf", "audio", "video"]

# Visibility controls who sees the agent in the picker. `public` is the
# default; `beta` adds a UI badge; `internal` hides from non-staff. The
# server still allows direct use of an internal agent if the caller
# names it, so this is presentation-only.
Visibility = Literal["public", "beta", "internal"]


class AgentTool(BaseModel):
    """One entry in an agent's curated tool palette.

    `default_enabled` is the initial state of the tool in a new thread.
    `user_toggleable` controls whether the frontend renders a toggle AND
    whether the server accepts overrides for this tool — overrides for a
    locked tool are rejected by the capabilities-service resolver, not
    silently ignored, so a caller can tell the difference between
    "ignored my request" and "got it".
    """

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    default_enabled: bool
    user_toggleable: bool


class AgentConstraints(BaseModel):
    """Runtime bounds on a single agent run. All fields optional — `None`
    means "no agent-level limit" (the platform may still impose one).
    """

    # Wall-clock cap for one run, after which the runner cancels the
    # graph. Long-running agents (deep research) override the platform
    # default (120s) up to a hard ceiling enforced by the runner.
    max_runtime_seconds: int | None = Field(default=None, gt=0, le=3600)

    # LangGraph node-iteration cap. Different from runtime — a stuck
    # tight loop hits this first; a slow tool hits runtime first.
    max_iterations: int | None = Field(default=None, gt=0, le=200)

    # False for agents that only emit a single final message (e.g. a
    # one-shot classifier). Frontend uses this to decide whether to
    # render a streaming caret or just a spinner.
    streams_partial_tokens: bool = True


class AgentPricing(BaseModel):
    """Per-1M-token costs surfaced through `underlying.pricing` for the
    optional cost-aware UI. Computed by the capabilities service from
    LiteLLM's per-token `input_cost_per_token` / `output_cost_per_token`;
    agent authors don't set this directly.
    """

    input_per_1m_usd: float = Field(ge=0)
    output_per_1m_usd: float = Field(ge=0)


class AgentSpec(BaseModel):
    """The capability declaration carried by every `BaseAgent` subclass.

    Frozen after construction so the registry can safely cache instances
    and the boot-time validator can rely on values not mutating mid-run.
    """

    model_config = {"frozen": True}

    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)
    icon: str | None = Field(default=None, max_length=32)

    # LiteLLM `model_name` (the key in litellm/config.yaml). The registry
    # validates against `/model/info` at boot.
    underlying_model: str = Field(min_length=1)

    # Modalities the AGENT accepts as input. Must be a subset of the
    # underlying model's modalities — validated at boot, not here.
    accepts_modalities: list[Modality] = Field(min_length=1)

    # Curated, ORDERED palette. UI renders toggles in this order.
    tools: list[AgentTool] = Field(default_factory=list)

    constraints: AgentConstraints | None = None

    visibility: Visibility = "public"

    @field_validator("tools")
    @classmethod
    def _tool_ids_unique(cls, tools: list[AgentTool]) -> list[AgentTool]:
        seen: set[str] = set()
        for t in tools:
            if t.id in seen:
                raise ValueError(f"duplicate tool id in spec: {t.id!r}")
            seen.add(t.id)
        return tools

    @field_validator("accepts_modalities")
    @classmethod
    def _modalities_unique(cls, ms: list[Modality]) -> list[Modality]:
        if len(set(ms)) != len(ms):
            raise ValueError("accepts_modalities contains duplicates")
        return ms
