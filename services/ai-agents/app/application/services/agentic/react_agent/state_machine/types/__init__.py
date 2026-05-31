"""Type definitions for state machine system."""

from react_agent.state_machine.types.config_types import SectionConfig, TransitionCondition
from react_agent.state_machine.types.state_types import SectionFlowState
from react_agent.state_machine.types.type_aliases import (
    AutoTransitionCondition,
    PromptPosition,
    SectionHook,
    SectionName,
    StateDict,
    StateValidator,
)

__all__ = [
    # Config types
    "SectionConfig",
    "TransitionCondition",
    # State types
    "SectionFlowState",
    # Type aliases
    "SectionName",
    "PromptPosition",
    "StateDict",
    "AutoTransitionCondition",
    "StateValidator",
    "SectionHook",
]
