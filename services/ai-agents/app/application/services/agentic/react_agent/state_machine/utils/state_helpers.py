"""State helper functions for section-based workflow management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

if TYPE_CHECKING:
    from react_agent.state_machine.types.config_types import SectionConfig


def get_effective_section(state: dict[str, Any], fallback_section: str) -> str:
    """Get the effective section from state with fallback.

    Args:
        state: State dictionary
        fallback_section: Section to use if current_section is not set

    Returns:
        Current section or fallback
    """
    return state.get("current_section") or fallback_section


def build_section_prompt_with_transitions(section_config: SectionConfig, current_section: str) -> str:
    """Build section prompt with transition context appended.

    Args:
        section_config: Configuration for the current section
        current_section: Name of the current section

    Returns:
        Complete section prompt with transition info if applicable
    """
    section_prompt = section_config.prompt
    allowed_transitions = section_config.allowed_transitions or []

    if allowed_transitions:
        transitions_info = (
            f"\n\n<SectionContext>\n"
            f"<CurrentSection>{current_section}</CurrentSection>\n"
            f"<AvailableTransitions>{', '.join(allowed_transitions)}</AvailableTransitions>\n"
            f"<FlowInstruction>Calling change_section does NOT stop the flow. "
            f"Continue executing the next steps immediately in the new section context.</FlowInstruction>\n"
            f"</SectionContext>"
        )
        section_prompt = section_prompt + transitions_info

    return section_prompt


def inject_section_prompt_into_request(request: ModelRequest, section_prompt: str) -> ModelRequest:
    """Create a modified request with section prompt injected as system message.

    Args:
        request: Original model request
        section_prompt: Section-specific prompt to inject

    Returns:
        Modified request with section prompt prepended
    """
    section_system_msg = SystemMessage(content=section_prompt)
    modified_messages = [section_system_msg] + request.messages
    return request.override(messages=modified_messages)


__all__ = [
    "get_effective_section",
    "build_section_prompt_with_transitions",
    "inject_section_prompt_into_request",
]
