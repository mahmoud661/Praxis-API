"""Model binding logic (domain layer)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest
    from langchain.agents.structured_output import OutputToolBinding, ResponseFormat
    from langchain_core.runnables import Runnable
    from langgraph.prebuilt.tool_node import ToolNode


def get_bound_model(
    request: "ModelRequest",
    tool_node: "ToolNode | None",
    structured_output_tools: dict[str, "OutputToolBinding"],
) -> tuple["Runnable", "ResponseFormat | None"]:
    """Bind model with tools and response format.

    Args:
        request: The model request containing model, tools, and response format.
        tool_node: Optional tool node containing available client-side tools.
        structured_output_tools: Dictionary of structured output tool bindings.

    Returns:
        Tuple of (bound model, effective response format).

    Raises:
        ValueError: If unknown tools or invalid response format configuration.
    """
    from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
    from langchain_core.tools import BaseTool

    # Import here to avoid circular dependency
    from react_agent.structured_output import supports_provider_strategy

    # Validate ONLY client-side tools that need to exist in tool_node
    # Build map of available client-side tools from the ToolNode
    # (which has already converted callables)
    available_tools_by_name = {}
    if tool_node:
        available_tools_by_name = tool_node.tools_by_name.copy()

    # Check if any requested tools are unknown CLIENT-SIDE tools
    unknown_tool_names = []
    for t in request.tools:
        # Only validate BaseTool instances (skip built-in dict tools)
        if isinstance(t, dict):
            continue
        if isinstance(t, BaseTool) and t.name not in available_tools_by_name:
            unknown_tool_names.append(t.name)

    if unknown_tool_names:
        available_tool_names = sorted(available_tools_by_name.keys())
        msg = (
            f"Middleware returned unknown tool names: {unknown_tool_names}\n\n"
            f"Available client-side tools: {available_tool_names}\n\n"
            "To fix this issue:\n"
            "1. Ensure the tools are passed to create_agent() via "
            "the 'tools' parameter\n"
            "2. If using custom middleware with tools, ensure "
            "they're registered via middleware.tools attribute\n"
            "3. Verify that tool names in ModelRequest.tools match "
            "the actual tool.name values\n"
            "Note: Built-in provider tools (dict format) can be added dynamically."
        )
        raise ValueError(msg)

    # Determine effective response format (auto-detect if needed)
    effective_response_format: "ResponseFormat | None"
    if isinstance(request.response_format, AutoStrategy):
        # User provided raw schema via AutoStrategy - auto-detect best strategy based on model
        if supports_provider_strategy(request.model, tools=request.tools):
            # Model supports provider strategy - use it
            effective_response_format = ProviderStrategy(schema=request.response_format.schema)
        else:
            # Model doesn't support provider strategy - use ToolStrategy
            effective_response_format = ToolStrategy(schema=request.response_format.schema)
    else:
        # User explicitly specified a strategy - preserve it
        effective_response_format = request.response_format

    # Build final tools list including structured output tools
    # request.tools now only contains BaseTool instances (converted from callables)
    # and dicts (built-ins)
    final_tools = list(request.tools)
    if isinstance(effective_response_format, ToolStrategy):
        # Add structured output tools to final tools list
        structured_tools = [info.tool for info in structured_output_tools.values()]
        final_tools.extend(structured_tools)

    # Bind model based on effective response format
    if isinstance(effective_response_format, ProviderStrategy):
        # (Backward compatibility) Use OpenAI format structured output
        kwargs = effective_response_format.to_model_kwargs()
        return (
            request.model.bind_tools(final_tools, strict=True, **kwargs, **request.model_settings),
            effective_response_format,
        )

    if isinstance(effective_response_format, ToolStrategy):
        # Current implementation requires that tools used for structured output
        # have to be declared upfront when creating the agent as part of the
        # response format. Middleware is allowed to change the response format
        # to a subset of the original structured tools when using ToolStrategy,
        # but not to add new structured tools that weren't declared upfront.
        # Compute output binding
        for tc in effective_response_format.schema_specs:
            if tc.name not in structured_output_tools:
                msg = (
                    f"ToolStrategy specifies tool '{tc.name}' "
                    "which wasn't declared in the original "
                    "response format when creating the agent."
                )
                raise ValueError(msg)

        # Force tool use if we have structured output tools
        tool_choice = "any" if structured_output_tools else request.tool_choice
        return (
            request.model.bind_tools(final_tools, tool_choice=tool_choice, **request.model_settings),
            effective_response_format,
        )

    # No structured output - standard model binding
    if final_tools:
        return (
            request.model.bind_tools(final_tools, tool_choice=request.tool_choice, **request.model_settings),
            None,
        )
    return request.model.bind(**request.model_settings), None
