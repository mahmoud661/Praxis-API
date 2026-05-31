"""Configuration types for section-based workflow control.

These use Pydantic models for validation, serialization, and better type safety.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from react_agent.state_machine.types.type_aliases import (
    AutoTransitionCondition,
    PromptPosition,
    SectionHook,
    SectionName,
    StateDict,
    StateValidator,
)


class TransitionCondition(BaseModel):
    """Conditional transition with priority."""

    target: SectionName
    condition: StateValidator
    priority: int = 0
    description: str = ""

    model_config = {"arbitrary_types_allowed": True}

    def evaluate(self, state: StateDict) -> bool:
        """Check if condition is met."""
        try:
            return self.condition(state)
        except Exception:
            return False


class SectionConfig(BaseModel):
    """Section configuration with prompt, tools, and transition rules."""

    name: SectionName
    prompt: str
    tools: list[Any] = Field(default_factory=list)
    allowed_transitions: list[SectionName] = Field(default_factory=list)
    required_state_fields: dict[str, type] = Field(default_factory=dict)
    auto_transition_conditions: AutoTransitionCondition | list[TransitionCondition] | None = None
    strict_validation: bool = True
    on_enter: SectionHook | None = None
    on_exit: SectionHook | None = None
    prompt_position: PromptPosition = "append"
    allowed_subagents: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    context_flags: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("name", "prompt")
    @classmethod
    def validate_not_empty(cls, v: str, info) -> str:
        """Validate field is not empty."""
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} cannot be empty")
        return v

    def validate_required_fields(self, state: StateDict) -> tuple[bool, list[str]]:
        """Validate required state fields are present and correct type."""
        missing = []
        section_data = state.get("section_data", {})

        for field, expected_type in self.required_state_fields.items():
            value = section_data.get(field)
            if value is None:
                missing.append(f"{field} (missing)")
            elif not isinstance(value, expected_type):
                missing.append(f"{field} (wrong type)")

        return len(missing) == 0, missing

    def can_transition_to(self, target: SectionName) -> bool:
        """Check if transition to target is allowed."""
        return not self.allowed_transitions or target in self.allowed_transitions

    def evaluate_auto_transitions(self, state: StateDict) -> SectionName | None:
        """Evaluate auto-transition conditions."""
        if not self.auto_transition_conditions:
            return None

        # Simple callable
        if callable(self.auto_transition_conditions):
            try:
                return self.auto_transition_conditions(state)
            except Exception:
                return None

        # Priority-based conditions
        for cond in sorted(self.auto_transition_conditions, key=lambda c: c.priority, reverse=True):
            if cond.evaluate(state):
                return cond.target
        return None

    def execute_hook(self, hook: SectionHook | None, state: StateDict) -> dict[str, Any] | None:
        """Execute a lifecycle hook safely."""
        if hook is None:
            return None
        try:
            return hook(state)
        except Exception:
            return None

    def execute_on_enter(self, state: StateDict) -> dict[str, Any] | None:
        """Execute on_enter hook."""
        return self.execute_hook(self.on_enter, state)

    def execute_on_exit(self, state: StateDict) -> dict[str, Any] | None:
        """Execute on_exit hook."""
        return self.execute_hook(self.on_exit, state)


__all__ = [
    "SectionConfig",
    "TransitionCondition",
]
