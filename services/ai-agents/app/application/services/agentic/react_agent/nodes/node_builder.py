"""Graph node building utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware
from langgraph._internal._runnable import RunnableCallable
from langgraph.graph.state import StateGraph
from langgraph.types import RetryPolicy

from react_agent.nodes.model_nodes import make_amodel_node, make_model_node

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from langchain.agents.structured_output import AutoStrategy, OutputToolBinding, ProviderStrategy, ToolStrategy
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import SystemMessage
    from langchain_core.tools import BaseTool
    from langgraph.prebuilt.tool_node import ToolNode
    from langgraph.typing import ContextT


def create_state_graph(
    resolved_state_schema: type,
    input_schema: type,
    output_schema: type,
    context_schema: type[ContextT] | None,
) -> StateGraph:
    """Create StateGraph with resolved schemas.

    Args:
        resolved_state_schema: Resolved state schema from middleware
        input_schema: Resolved input schema
        output_schema: Resolved output schema
        context_schema: Optional context schema

    Returns:
        StateGraph instance
    """
    return StateGraph(
        state_schema=resolved_state_schema,
        input_schema=input_schema,
        output_schema=output_schema,
        context_schema=context_schema,
    )


def add_model_node(
    graph: StateGraph,
    model: BaseChatModel,
    default_tools: list[BaseTool | dict[str, Any]],
    system_message: SystemMessage | None,
    initial_response_format: ToolStrategy | ProviderStrategy | AutoStrategy | None,
    wrap_model_call_handler: Callable | None,
    awrap_model_call_handler: Callable | None,
    tool_node: ToolNode | None,
    structured_output_tools: dict[str, OutputToolBinding],
    name: str | None,
) -> None:
    """Add model node to graph with sync/async support.

    Args:
        graph: StateGraph to add node to
        model: Chat model instance
        default_tools: Default tools for model
        system_message: Optional system message
        initial_response_format: Response format strategy
        wrap_model_call_handler: Composed sync model handler
        awrap_model_call_handler: Composed async model handler
        tool_node: Optional tool node
        structured_output_tools: Structured output tool bindings
        name: Optional agent name
    """
    model_node = make_model_node(
        model=model,
        default_tools=default_tools,
        system_message=system_message,
        initial_response_format=initial_response_format,
        wrap_model_call_handler=wrap_model_call_handler,
        tool_node=tool_node,
        structured_output_tools=structured_output_tools,
        name=name,
    )

    amodel_node = make_amodel_node(
        model=model,
        default_tools=default_tools,
        system_message=system_message,
        initial_response_format=initial_response_format,
        awrap_model_call_handler=awrap_model_call_handler,
        tool_node=tool_node,
        structured_output_tools=structured_output_tools,
        name=name,
    )

    # Use sync or async based on model capabilities
    graph.add_node("model", RunnableCallable(model_node, amodel_node, trace=False))


def add_tool_node(
    graph: StateGraph,
    tool_node: ToolNode | None,
) -> None:
    """Add tool node to graph if tools exist.

    Args:
        graph: StateGraph to add node to
        tool_node: Optional tool node
    """
    if tool_node is not None:
        graph.add_node("tools", tool_node, retry=RetryPolicy(max_attempts=3))


def _add_hook_node(
    graph: StateGraph,
    middleware: AgentMiddleware,
    hook_name: str,
    resolved_state_schema: type,
) -> None:
    """Add a middleware hook node if the hook is implemented.

    Args:
        graph: StateGraph to add node to
        middleware: Middleware instance
        hook_name: Name of the hook (before_agent, after_model, etc.)
        resolved_state_schema: Resolved state schema
    """
    sync_method_name = hook_name
    async_method_name = f"a{hook_name}"

    base_sync = getattr(AgentMiddleware, sync_method_name)
    base_async = getattr(AgentMiddleware, async_method_name)

    impl_sync = getattr(middleware.__class__, sync_method_name)
    impl_async = getattr(middleware.__class__, async_method_name)

    if impl_sync is not base_sync or impl_async is not base_async:
        sync_fn = getattr(middleware, sync_method_name) if impl_sync is not base_sync else None
        async_fn = getattr(middleware, async_method_name) if impl_async is not base_async else None

        node = RunnableCallable(sync_fn, async_fn, trace=False)
        graph.add_node(
            f"{middleware.name}.{hook_name}",
            node,
            input_schema=resolved_state_schema,
        )


def add_middleware_nodes(
    graph: StateGraph,
    middleware_by_hook: dict[str, list[AgentMiddleware]],
    resolved_state_schema: type,
) -> None:
    """Add middleware nodes to graph.

    collect_middleware_by_hook intentionally places the same middleware instance
    in multiple hook lists when it implements multiple hooks (e.g. before_model
    AND after_model).  Deduplicate by identity here so each middleware is
    processed exactly once — otherwise graph.add_node raises "already present"
    for shared hook-node names like ``HandoffMiddleware.before_model``.
    """
    seen: set[int] = set()
    all_middleware: list[AgentMiddleware] = []
    for m in (
        middleware_by_hook["before_agent"]
        + middleware_by_hook["before_model"]
        + middleware_by_hook["after_model"]
        + middleware_by_hook["after_agent"]
    ):
        if id(m) not in seen:
            seen.add(id(m))
            all_middleware.append(m)

    for m in all_middleware:
        for hook in ["before_agent", "before_model", "after_model", "after_agent"]:
            _add_hook_node(graph, m, hook, resolved_state_schema)


__all__ = [
    "add_middleware_nodes",
    "add_model_node",
    "add_tool_node",
    "create_state_graph",
]
