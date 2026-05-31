"""Section transition tool for agent-initiated section changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from react_agent.state_machine.core.manager import SectionManager


def create_change_section_tool(section_manager: SectionManager) -> StructuredTool:
    """Create the change_section tool for agent-initiated transitions.

    This tool allows the agent to explicitly request section changes.
    The section manager validates the transition before executing it.

    Args:
        section_manager: SectionManager instance for validation

    Returns:
        StructuredTool for changing sections

    Example:
        Agent calls: change_section(target_section="preferences", reason="User info collected")
    """

    def change_section(
        target_section: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
        reason: str | None = None,
    ) -> Command:
        """Change the current section to a new section.

        Use this tool when you need to move to a different section of the workflow.
        For example, after collecting user information, transition to the preferences section.

        Args:
            target_section: Name of the section to transition to
            reason: Brief explanation of why you're changing sections

        Returns:
            Command to update the section state
        """
        # Get available sections for validation
        available_sections = section_manager.get_section_names()

        if target_section not in available_sections:
            error_msg = f"Section '{target_section}' not found. " f"Available sections: {', '.join(available_sections)}"
            logger.warning(error_msg)

            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            content=f"Error: {error_msg}",
                            name="change_section",
                            status="error",
                        )
                    ]
                }
            )

        # Calculate state update
        current_section = state.get("current_section")
        update_dict = {"current_section": target_section}

        message_content = f"Successfully changed section from '{current_section}' to '{target_section}'."
        if reason:
            message_content += f" Reason: {reason}"

        logger.info(message_content)

        return Command(
            update={
                **update_dict,  # Contains current_section update
                "messages": [
                    ToolMessage(
                        tool_call_id=tool_call_id,
                        content=message_content,
                        name="change_section",
                        status="success",
                    )
                ],
            }
        )

    async def achange_section(
        target_section: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
        reason: str | None = None,
    ) -> Command:
        """Async version of change_section."""
        return change_section(target_section, state, tool_call_id, reason)

    # Get available sections for description
    section_list = section_manager.get_section_names()

    return StructuredTool.from_function(
        name="change_section",
        func=change_section,
        coroutine=achange_section,
        description=(
            f"Change to a different section of the workflow. "
            f"Use this when you've completed the current section's tasks and need to move forward. "
            f"Available sections: {', '.join(section_list)}. "
            f"Always provide a clear reason for the transition."
        ),
    )


__all__ = ["create_change_section_tool"]
