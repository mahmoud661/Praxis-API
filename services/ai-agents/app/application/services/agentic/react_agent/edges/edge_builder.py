"""Graph edge building utilities."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from langgraph._internal._runnable import RunnableCallable
from langgraph.constants import END, START
from langgraph.graph.state import StateGraph

from react_agent.edges.middleware_jump_edge import add_middleware_edge
from react_agent.edges.model_to_model import make_model_to_model_edge
from react_agent.edges.model_to_tools import make_model_to_tools_edge
from react_agent.edges.tools_to_model import make_tools_to_model_edge
from react_agent.utils.schema_resolver import get_can_jump_to

if TYPE_CHECKING:
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain.agents.structured_output import OutputToolBinding, ResponseFormat
    from langgraph.prebuilt.tool_node import ToolNode


def add_start_edge(
    graph: StateGraph,
    entry_node: str,
) -> None:
    """Add edge from START to entry node.

    Args:
        graph: StateGraph to add edge to
        entry_node: Node to connect from START
    """
    graph.add_edge(START, entry_node)


def add_tool_edges(
    graph: StateGraph,
    tool_node: ToolNode,
    loop_entry_node: str,
    loop_exit_node: str,
    exit_node: str,
    response_format: ResponseFormat | None,
    structured_output_tools: dict[str, OutputToolBinding],
) -> None:
    """Add conditional edges for tool routing.

    Args:
        graph: StateGraph to add edges to
        tool_node: Tool node instance
        loop_entry_node: Node to loop back to
        loop_exit_node: Node at end of iteration
        exit_node: Final exit node
        response_format: Response format configuration
        structured_output_tools: Structured output tool bindings
    """
    # Only include exit_node in destinations if any tool has return_direct=True
    # or if there are structured output tools
    tools_to_model_destinations = [loop_entry_node]
    if any(tool.return_direct for tool in tool_node.tools_by_name.values()) or structured_output_tools:
        tools_to_model_destinations.append(exit_node)

    graph.add_conditional_edges(
        "tools",
        RunnableCallable(
            make_tools_to_model_edge(
                tool_node=tool_node,
                model_destination=loop_entry_node,
                structured_output_tools=structured_output_tools,
                end_destination=exit_node,
            ),
            trace=False,
        ),
        tools_to_model_destinations,
    )

    # base destinations are tools and exit_node
    # we add the loop_entry node to edge destinations if:
    # - there is an after model hook(s) -- allows jump_to to model
    #   potentially artificially injected tool messages, ex HITL
    # - there is a response format -- to allow for jumping to model to handle
    #   regenerating structured output tool calls
    model_to_tools_destinations = ["tools", exit_node]
    if response_format or loop_exit_node != "model":
        model_to_tools_destinations.append(loop_entry_node)

    graph.add_conditional_edges(
        loop_exit_node,
        RunnableCallable(
            make_model_to_tools_edge(
                model_destination=loop_entry_node,
                structured_output_tools=structured_output_tools,
                end_destination=exit_node,
            ),
            trace=False,
        ),
        model_to_tools_destinations,
    )


def add_structured_output_edges(
    graph: StateGraph,
    loop_entry_node: str,
    loop_exit_node: str,
    exit_node: str,
) -> None:
    """Add conditional edges for structured output (no tools).

    Args:
        graph: StateGraph to add edges to
        loop_entry_node: Node to loop back to
        loop_exit_node: Node at end of iteration
        exit_node: Final exit node
    """
    graph.add_conditional_edges(
        loop_exit_node,
        RunnableCallable(
            make_model_to_model_edge(
                model_destination=loop_entry_node,
                end_destination=exit_node,
            ),
            trace=False,
        ),
        [loop_entry_node, exit_node],
    )


def add_simple_edge(
    graph: StateGraph,
    loop_exit_node: str,
    exit_node: str,
    middleware_by_hook: dict[str, list[AgentMiddleware]],
    loop_entry_node: str,
) -> None:
    """Add simple edge when no tools or structured output.

    Args:
        graph: StateGraph to add edge to
        loop_exit_node: Node at end of iteration
        exit_node: Final exit node
        middleware_by_hook: Middleware grouped by hook type
        loop_entry_node: Node to loop back to
    """
    middleware_w_after_model = middleware_by_hook["after_model"]

    if loop_exit_node == "model":
        # If no tools and no after_model, go directly to exit_node
        graph.add_edge(loop_exit_node, exit_node)
    else:
        # No tools but we have after_model - connect after_model to exit_node
        add_middleware_edge(
            graph,
            name=f"{middleware_w_after_model[0].name}.after_model",
            default_destination=exit_node,
            model_destination=loop_entry_node,
            end_destination=exit_node,
            can_jump_to=get_can_jump_to(middleware_w_after_model[0], "after_model"),
        )


def _add_middleware_chain(
    graph: StateGraph,
    middleware_list: list[AgentMiddleware],
    hook_name: str,
    default_destination: str | None,
    loop_entry_node: str,
    exit_node: str,
    reverse: bool = False,
) -> None:
    """Helper to add a chain of middleware edges.

    Args:
        graph: StateGraph to add edges to
        middleware_list: List of middleware to chain
        hook_name: Name of the hook (before_agent, after_model, etc.)
        default_destination: Default destination for the last middleware
        loop_entry_node: Node to loop back to
        exit_node: Final exit node
        reverse: If True, process middleware in reverse order
    """
    if not middleware_list:
        return

    items = list(reversed(middleware_list)) if reverse else middleware_list

    for m1, m2 in itertools.pairwise(items):
        next_dest = f"{m2.name}.{hook_name}"
        add_middleware_edge(
            graph,
            name=f"{m1.name}.{hook_name}",
            default_destination=next_dest,
            model_destination=loop_entry_node,
            end_destination=exit_node,
            can_jump_to=get_can_jump_to(m1, hook_name),
        )

    if default_destination is not None:
        last_middleware = items[-1]
        add_middleware_edge(
            graph,
            name=f"{last_middleware.name}.{hook_name}",
            default_destination=default_destination,
            model_destination=loop_entry_node,
            end_destination=exit_node,
            can_jump_to=get_can_jump_to(last_middleware, hook_name),
        )


def add_middleware_edges(
    graph: StateGraph,
    middleware_by_hook: dict[str, list[AgentMiddleware]],
    loop_entry_node: str,
    exit_node: str,
) -> None:
    """Add all middleware edges to graph."""
    middleware_w_before_agent = middleware_by_hook["before_agent"]
    middleware_w_before_model = middleware_by_hook["before_model"]
    middleware_w_after_model = middleware_by_hook["after_model"]
    middleware_w_after_agent = middleware_by_hook["after_agent"]

    _add_middleware_chain(graph, middleware_w_before_agent, "before_agent", loop_entry_node, loop_entry_node, exit_node)

    _add_middleware_chain(graph, middleware_w_before_model, "before_model", "model", loop_entry_node, exit_node)

    if middleware_w_after_model:
        graph.add_edge("model", f"{middleware_w_after_model[-1].name}.after_model")
        _add_middleware_chain(
            graph,
            middleware_w_after_model,
            "after_model",
            default_destination=None,
            loop_entry_node=loop_entry_node,
            exit_node=exit_node,
            reverse=True,
        )

    if middleware_w_after_agent:
        _add_middleware_chain(
            graph, middleware_w_after_agent, "after_agent", END, loop_entry_node, exit_node, reverse=True
        )


__all__ = [
    "add_middleware_edges",
    "add_simple_edge",
    "add_start_edge",
    "add_structured_output_edges",
    "add_tool_edges",
]
