"""State type definitions for section-based workflow tracking.

These use TypedDict to maintain compatibility with LangGraph's state system.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _last_value(left: Any, right: Any) -> Any:
    """Reducer that takes the last value when multiple updates occur."""
    if isinstance(right, list):
        return right[-1] if right else left
    return right


class SectionFlowState(TypedDict, total=False):
    """State extension for section-based flow control.

    This state is designed to be merged with existing agent states
    to add section flow capabilities.

    Attributes:
        current_section: Name of the currently active section (uses last value when multiple updates occur)
        section_data: Shared data across sections (per-conversation state)
        visited_sections: List of sections visited in this conversation
    """

    current_section: Annotated[str, _last_value]
    section_data: dict[str, Any]
    visited_sections: list[str]


__all__ = [
    "SectionFlowState",
]
