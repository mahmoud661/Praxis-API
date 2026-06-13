"""Unit tests for `AgentSpec` validation.

The spec is the input contract for the registry — invalid specs would
silently misrepresent capabilities to the frontend, so validation runs
at construction (Pydantic). Tests cover the non-obvious rules:
duplicate-tool-id rejection, duplicate-modality rejection, and the
basic shape constraints (min lengths, allowed literals)."""

import pytest
from pydantic import ValidationError

from app.application.services.agentic.agent_spec import (
    AgentConstraints,
    AgentSpec,
    AgentTool,
)


def _minimal_spec(**overrides) -> AgentSpec:
    base = {
        "id": "general",
        "display_name": "General",
        "description": "A general-purpose agent.",
        "underlying_model": "praxis-default",
        "accepts_modalities": ["text"],
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_minimal_spec_constructs_with_defaults():
    spec = _minimal_spec()
    assert spec.id == "general"
    assert spec.tools == []
    assert spec.visibility == "public"
    assert spec.icon is None


def test_duplicate_tool_ids_rejected():
    with pytest.raises(ValidationError) as exc:
        _minimal_spec(
            tools=[
                AgentTool(
                    id="web_search",
                    label="Web search",
                    default_enabled=True,
                    user_toggleable=True,
                ),
                AgentTool(
                    id="web_search",  # duplicate
                    label="Other label",
                    default_enabled=False,
                    user_toggleable=False,
                ),
            ]
        )
    assert "duplicate tool id" in str(exc.value)


def test_duplicate_modalities_rejected():
    with pytest.raises(ValidationError) as exc:
        _minimal_spec(accepts_modalities=["text", "image", "text"])
    assert "duplicates" in str(exc.value)


def test_empty_modalities_rejected():
    # Every agent accepts at least text — empty would mean "no input
    # at all" which doesn't make sense for a chat agent.
    with pytest.raises(ValidationError):
        _minimal_spec(accepts_modalities=[])


def test_unknown_modality_rejected():
    with pytest.raises(ValidationError):
        _minimal_spec(accepts_modalities=["text", "telepathy"])  # type: ignore[list-item]


def test_unknown_visibility_rejected():
    with pytest.raises(ValidationError):
        _minimal_spec(visibility="staff-only")  # type: ignore[arg-type]


def test_spec_is_frozen():
    spec = _minimal_spec()
    with pytest.raises(ValidationError):
        spec.id = "other"  # type: ignore[misc]


def test_constraints_bounds_enforced():
    # Runtime cap ceilinged at 1 hour.
    with pytest.raises(ValidationError):
        AgentConstraints(max_runtime_seconds=4000)
    # Negative or zero rejected.
    with pytest.raises(ValidationError):
        AgentConstraints(max_runtime_seconds=0)
    # Iterations also bounded.
    with pytest.raises(ValidationError):
        AgentConstraints(max_iterations=500)
