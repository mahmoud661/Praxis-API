"""Response format conversion and structured output handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.structured_output import AutoStrategy, OutputToolBinding, ProviderStrategy, ToolStrategy

if TYPE_CHECKING:
    from langchain.agents.structured_output import ResponseFormat, ResponseT


def convert_response_format(
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | None,
) -> tuple[ToolStrategy | ProviderStrategy | AutoStrategy | None, ToolStrategy | None,]:
    """Convert response format to internal strategy representation.

    Args:
        response_format: User-provided response format (can be raw schema, strategy, or None)

    Returns:
        Tuple of (initial_response_format, tool_strategy_for_setup)
        - initial_response_format: Strategy to use for model execution
        - tool_strategy_for_setup: ToolStrategy for upfront tool setup (if needed)
    """
    # No response format requested
    if response_format is None:
        return None, None

    # Preserve explicitly requested strategies
    if isinstance(response_format, (ToolStrategy, ProviderStrategy)):
        tool_strategy_for_setup = response_format if isinstance(response_format, ToolStrategy) else None
        return response_format, tool_strategy_for_setup

    # AutoStrategy provided - preserve it for later auto-detection
    if isinstance(response_format, AutoStrategy):
        tool_strategy_for_setup = ToolStrategy(schema=response_format.schema)
        return response_format, tool_strategy_for_setup

    # Raw schema - wrap in AutoStrategy to enable auto-detection
    initial_response_format = AutoStrategy(schema=response_format)
    tool_strategy_for_setup = ToolStrategy(schema=response_format)
    return initial_response_format, tool_strategy_for_setup


def create_structured_output_tools(
    tool_strategy: ToolStrategy | None,
) -> dict[str, OutputToolBinding]:
    """Create structured output tool bindings from tool strategy.

    Args:
        tool_strategy: ToolStrategy containing schema specifications

    Returns:
        Dictionary mapping tool names to OutputToolBinding instances
    """
    if tool_strategy is None:
        return {}

    structured_output_tools: dict[str, OutputToolBinding] = {}
    for response_schema in tool_strategy.schema_specs:
        structured_tool_info = OutputToolBinding.from_schema_spec(response_schema)
        structured_output_tools[structured_tool_info.tool.name] = structured_tool_info

    return structured_output_tools


__all__ = [
    "convert_response_format",
    "create_structured_output_tools",
]
