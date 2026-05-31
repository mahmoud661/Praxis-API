"""Section flow middleware for React agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from react_agent.state_machine.core.manager import SectionManager
from react_agent.state_machine.types.config_types import SectionConfig
from react_agent.state_machine.types.state_types import SectionFlowState
from react_agent.state_machine.types.type_aliases import SectionName
from react_agent.state_machine.utils.state_helpers import (
    build_section_prompt_with_transitions,
    get_effective_section,
    inject_section_prompt_into_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from langgraph.runtime import Runtime


class SectionFlowMiddleware(AgentMiddleware):
    """Middleware that provides section-based flow control for React agents.

    This middleware:
    - Initializes section state on first run
    - Filters tools to only show current section's tools
    - Appends section-specific prompts to system message (cache-friendly)
    - Evaluates auto-transition conditions after each model call
    - Provides change_section tool for agent-initiated transitions

    Example:
        ```python
        sections = {
            "collect_info": SectionConfig(
                name="collect_info",
                prompt="Focus on collecting user information.",
                tools=[get_user_info_tool],
                allowed_transitions=["preferences"],
            ),
            "preferences": SectionConfig(
                name="preferences",
                prompt="Ask about preferences.",
                tools=[get_preferences_tool],
                allowed_transitions=["select_car"],
            ),
        }

        middleware = SectionFlowMiddleware(
            sections=sections,
            initial_section="collect_info",
        )

        agent = create_react_agent(
            model="gpt-4",
            system_prompt="You are a helpful assistant.",
            middleware=[middleware],
        )
        ```
    """

    @property
    def state_schema(self) -> type[SectionFlowState]:
        """Return state schema for this middleware."""
        return SectionFlowState

    def __init__(
        self,
        sections: dict[SectionName, SectionConfig] | None = None,
        initial_section: SectionName | None = None,
        strict_validation: bool = True,
        include_transition_tool: bool = True,
        section_manager: SectionManager | None = None,
        fallback_section: SectionName | None = None,
        global_tools: list[Any] | None = None,
        subagent_graphs: dict | None = None,
        all_middleware: list[Any] | None = None,
    ):
        """Initialize section flow middleware.

        Args:
            sections: Dictionary mapping section names to configurations (ignored if section_manager provided)
            initial_section: Name of the starting section (ignored if section_manager provided)
            strict_validation: Whether to enforce validation by default (ignored if section_manager provided)
            include_transition_tool: Whether to provide change_section tool to agent
            section_manager: Optional pre-configured SectionManager instance. If provided, sections/initial_section/strict_validation are ignored.
            fallback_section: Section to use if current section not found (for handling removed sections in production)
            global_tools: List of global tools available to all sections
            subagent_graphs: Dictionary of available subagent graphs for task tool filtering
            all_middleware: List of all middleware to collect tools from (for proper priority handling)
        """
        super().__init__()

        # Store tool filtering parameters
        self.global_tools = global_tools or []
        self.subagent_graphs = subagent_graphs or {}
        self.all_middleware = all_middleware or []
        self._section_tool_cache = {}  # Cache filtered tools per section

        # Use provided section_manager or create new one
        if section_manager is not None:
            self.section_manager = section_manager
        else:
            if sections is None or initial_section is None:
                raise ValueError("Either section_manager or both sections and initial_section must be provided")

            self.section_manager = SectionManager(
                sections=sections,
                initial_section=initial_section,
                strict_validation=strict_validation,
                fallback_section=fallback_section,
            )

        # Store initial section for state initialization
        self.initial_section = self.section_manager.initial_section

        # Import here to avoid circular dependency
        if include_transition_tool:
            from react_agent.state_machine.tools.change_section import create_change_section_tool

            self.tools = [create_change_section_tool(self.section_manager)]
        else:
            self.tools = []

    def before_model(self, state: SectionFlowState, runtime: Runtime | None = None) -> dict[str, Any] | None:
        """Initialize section state and update ToolNode with section-specific tools."""
        current_section = state.get("current_section")

        if current_section is None:
            return {"current_section": self.initial_section, "section_data": {}, "visited_sections": []}

        # Check if current section exists, use fallback if removed
        _, used_fallback = self.section_manager.get_section_with_fallback(current_section)
        if used_fallback:
            self._section_tool_cache.clear()
            return {"current_section": self.section_manager.fallback_section}

        # Track visited sections
        visited_sections = state.get("visited_sections", [])
        if current_section not in visited_sections:
            visited_sections = visited_sections + [current_section]

        # Check for auto-transitions before model runs
        target_section = self.section_manager.evaluate_auto_transitions(state)

        if target_section and target_section != current_section:
            # Add target section to visited_sections when transitioning
            updated_visited = (
                visited_sections + [target_section] if target_section not in visited_sections else visited_sections
            )
            self._section_tool_cache.clear()
            return {"current_section": target_section, "visited_sections": updated_visited}

        # Update visited sections if changed
        if current_section not in state.get("visited_sections", []):
            return {"visited_sections": visited_sections}

        return None

    async def abefore_model(self, state: SectionFlowState, runtime: Runtime | None = None) -> dict[str, Any] | None:
        """Async version: Initialize section state if missing before model runs."""
        return self.before_model(state, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject section-specific prompt as separate system message.

        Args:
            request: Model request
            handler: Next handler in the chain

        Returns:
            Model response
        """
        state = request.state

        # Get current section with fallback to initial
        current_section = get_effective_section(state, self.initial_section)

        # Get section config with fallback for removed sections
        section_config, _ = self.section_manager.get_section_with_fallback(current_section)

        # Apply section-specific tool filtering to the request
        section_tools = self._get_section_tools(current_section)
        if section_tools and hasattr(request, "tools"):
            request.tools = section_tools

        # Call next handler with request
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Async version: Inject section-specific prompt as separate system message.

        Args:
            request: Model request
            handler: Next handler in the chain

        Returns:
            Model response
        """
        state = request.state

        # Get current section with fallback to initial
        current_section = get_effective_section(state, self.initial_section)

        # Get section config with fallback for removed sections
        section_config, _ = self.section_manager.get_section_with_fallback(current_section)

        # Build section prompt with transitions
        section_prompt = build_section_prompt_with_transitions(section_config, current_section)

        # Inject section prompt into request
        modified_request = inject_section_prompt_into_request(request, section_prompt)

        # Override tools with section-specific tools
        section_tools = self._get_section_tools(current_section)
        modified_request.tools = section_tools

        # Call next handler with modified request
        return await handler(modified_request)

    def _get_section_tools(self, current_section: SectionName) -> list[Any]:
        """Get filtered tools for the current section.

        Only includes:
        1. Section-specific tools from section.tools (string tool names must be explicitly listed)
        2. Required middleware tools (change_section, task)
        3. Global tools that override section tools with same name

        Args:
            current_section: Current section name

        Returns:
            List of tools available for this section
        """
        # Check cache first
        if current_section in self._section_tool_cache:
            return self._section_tool_cache[current_section]

        # Get section config
        section_config, _ = self.section_manager.get_section_with_fallback(current_section)
        section_tools = section_config.tools or []

        # Build tool dictionary - only include tools that are explicitly listed in this section
        tools_dict = {}

        # 1. Process section-specific tools - ONLY those explicitly listed in this section
        for tool in section_tools:
            if isinstance(tool, str):
                if tool == "task":
                    # Always provide task tool, but filter subagents shown
                    task_tool = self._get_task_tool_with_filtered_description(section_config)
                    if task_tool:
                        tools_dict[tool] = task_tool
                else:
                    # Look for this string tool in middleware tools
                    # CRITICAL: Only include if explicitly listed in this section's tools
                    tool_found = False
                    for middleware in self.all_middleware:
                        middleware_tools = getattr(middleware, "tools", [])
                        for mw_tool in middleware_tools:
                            mw_tool_name = getattr(mw_tool, "name", getattr(mw_tool, "__name__", str(mw_tool)))
                            if mw_tool_name == tool:
                                tools_dict[tool] = mw_tool
                                tool_found = True
                                break
                        if tool_found:
                            break

                    # Tool not found
            else:
                # Real tool object from section
                tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))

                # Check if there's a global tool that should override this
                global_override = None
                for global_tool in self.global_tools:
                    global_tool_name = getattr(global_tool, "name", getattr(global_tool, "__name__", str(global_tool)))
                    if global_tool_name == tool_name:
                        global_override = global_tool
                        break

                # Use global tool if it exists, otherwise use section tool
                tools_dict[tool_name] = global_override if global_override else tool

        # 2. Always add change_section tool (required for transitions) - this is a special case
        for tool in self.tools:
            tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            tools_dict[tool_name] = tool

        filtered_tools = list(tools_dict.values())

        # Cache the result
        self._section_tool_cache[current_section] = filtered_tools
        return filtered_tools

    def _get_task_tool_with_filtered_description(self, section_config: SectionConfig) -> Any | None:
        """Get task tool with description filtered to section's allowed subagents.

        Always returns the task tool, but modifies the description to only show
        subagents that are allowed in the current section.

        Args:
            section_config: Current section configuration

        Returns:
            Task tool with filtered subagent description
        """
        # Get the original task tool from middleware
        original_task_tool = None
        for middleware in self.all_middleware:
            middleware_tools = getattr(middleware, "tools", [])
            for tool in middleware_tools:
                if getattr(tool, "name", "") == "task":
                    original_task_tool = tool
                    break
            if original_task_tool:
                break

        if not original_task_tool:
            return None

        # Get allowed subagents for this section
        allowed_subagents = getattr(section_config, "allowed_subagents", [])

        if not allowed_subagents:
            # If no allowed subagents specified, show all available
            return original_task_tool

        # Convert subagent_graphs from list to dict for easier filtering
        subagent_dict = {}
        if isinstance(self.subagent_graphs, list):
            for subagent in self.subagent_graphs:
                if isinstance(subagent, dict) and "name" in subagent:
                    subagent_dict[subagent["name"]] = subagent
        elif isinstance(self.subagent_graphs, dict):
            subagent_dict = self.subagent_graphs
        else:
            return original_task_tool

        # Filter to allowed subagents
        filtered_subagents = {k: v for k, v in subagent_dict.items() if k in allowed_subagents}

        if not filtered_subagents:
            return original_task_tool

        # Create a copy of the task tool with modified description
        try:
            from copy import deepcopy

            filtered_task_tool = deepcopy(original_task_tool)

            # Update description to show only allowed subagents
            allowed_list = list(filtered_subagents.keys())
            filtered_description = f"Launch an ephemeral subagent to handle complex tasks. Available subagents for this section: {', '.join(allowed_list)}"

            # Update the tool description
            filtered_task_tool.description = filtered_description
            return filtered_task_tool

        except Exception:
            return original_task_tool

    def clear_tool_cache(self):
        """Clear the section tool cache. Call this if sections are modified at runtime."""
        self._section_tool_cache.clear()


__all__ = ["SectionFlowMiddleware"]
