from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware
from langgraph.prebuilt.tool_node import ToolNode

from react_agent.utils.tool_call_chain import chain_async_tool_call_wrappers, chain_tool_call_wrappers

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

    from langchain.agents.middleware.types import StateT_co
    from langchain_core.tools import BaseTool
    from langgraph.typing import ContextT


def collect_middleware_with_tool_wrappers(
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]],
) -> tuple[list[AgentMiddleware[StateT_co, ContextT]], list[AgentMiddleware[StateT_co, ContextT]],]:
    """Collect middleware with tool call wrappers."""
    middleware_w_wrap_tool_call = [
        m
        for m in middleware
        if m.__class__.wrap_tool_call is not AgentMiddleware.wrap_tool_call
        or m.__class__.awrap_tool_call is not AgentMiddleware.awrap_tool_call
    ]

    middleware_w_awrap_tool_call = [
        m
        for m in middleware
        if m.__class__.awrap_tool_call is not AgentMiddleware.awrap_tool_call
        or m.__class__.wrap_tool_call is not AgentMiddleware.wrap_tool_call
    ]

    return middleware_w_wrap_tool_call, middleware_w_awrap_tool_call


def create_tool_wrappers(
    middleware_w_wrap_tool_call: list[AgentMiddleware[StateT_co, ContextT]],
    middleware_w_awrap_tool_call: list[AgentMiddleware[StateT_co, ContextT]],
) -> tuple[Callable | None, Callable | None]:
    """Create composed tool call wrappers from middleware."""
    wrap_tool_call_wrapper = None
    if middleware_w_wrap_tool_call:
        wrappers = [m.wrap_tool_call for m in middleware_w_wrap_tool_call]
        wrap_tool_call_wrapper = chain_tool_call_wrappers(wrappers)

    awrap_tool_call_wrapper = None
    if middleware_w_awrap_tool_call:
        async_wrappers = [m.awrap_tool_call for m in middleware_w_awrap_tool_call]
        awrap_tool_call_wrapper = chain_async_tool_call_wrappers(async_wrappers)

    return wrap_tool_call_wrapper, awrap_tool_call_wrapper


def setup_tools(
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None,
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]],
    wrap_tool_call_wrapper: Callable | None,
    awrap_tool_call_wrapper: Callable | None,
    section_manager=None,
    filter_tools_by_sections: bool = False,
) -> tuple[ToolNode | None, list[BaseTool | dict[str, Any]]]:
    """Setup tool node and default tools list."""
    if tools is None:
        tools = []

    middleware_tools = [t for m in middleware for t in getattr(m, "tools", [])]

    if filter_tools_by_sections and section_manager:
        filtered_middleware_tools = _filter_tools_by_sections(middleware_tools, section_manager)
    else:
        filtered_middleware_tools = middleware_tools

    built_in_tools = [t for t in tools if isinstance(t, dict)]
    regular_tools = [t for t in tools if not isinstance(t, dict)]
    available_tools = filtered_middleware_tools + regular_tools

    tool_node = (
        ToolNode(
            tools=available_tools,
            wrap_tool_call=wrap_tool_call_wrapper,
            awrap_tool_call=awrap_tool_call_wrapper,
        )
        if available_tools
        else None
    )

    if tool_node:
        default_tools = list(tool_node.tools_by_name.values()) + built_in_tools
    else:
        default_tools = list(built_in_tools)

    return tool_node, default_tools


def _filter_tools_by_sections(middleware_tools, section_manager):
    """Filter middleware tools to only include ones used by sections."""
    section_tool_names = set()

    for section_config in section_manager.sections.values():
        for tool in section_config.tools or []:
            if isinstance(tool, str):
                section_tool_names.add(tool)
            else:
                tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
                section_tool_names.add(tool_name)

    section_tool_names.add("change_section")

    filtered_tools = []
    for tool in middleware_tools:
        tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
        if tool_name in section_tool_names:
            filtered_tools.append(tool)

    return filtered_tools


__all__ = [
    "collect_middleware_with_tool_wrappers",
    "create_tool_wrappers",
    "setup_tools",
]
