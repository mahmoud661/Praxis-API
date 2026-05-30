"""React agent graph builder - complete implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage

from react_agent.edges.edge_builder import (
    add_middleware_edges,
    add_simple_edge,
    add_start_edge,
    add_structured_output_edges,
    add_tool_edges,
)
from react_agent.nodes.node_builder import (
    add_middleware_nodes,
    add_model_node,
    add_tool_node,
    create_state_graph,
)
from react_agent.routing_config import determine_routing_nodes
from react_agent.setup import collect_middleware_with_tool_wrappers, create_tool_wrappers, setup_tools
from react_agent.structured_output.handler import convert_response_format, create_structured_output_tools
from react_agent.utils.handler_factory import create_model_call_handlers
from react_agent.utils.middleware_collection import collect_middleware_by_hook
from react_agent.utils.middleware_validation import validate_middleware
from react_agent.utils.schema_helpers import resolve_state_schemas

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

    from langchain.agents.middleware.types import (
        AgentMiddleware,
        AgentState,
        ResponseT,
        StateT_co,
        _InputAgentState,
        _OutputAgentState,
    )
    from langchain.agents.structured_output import (
        AutoStrategy,
        OutputToolBinding,
        ProviderStrategy,
        ResponseFormat,
        ToolStrategy,
    )
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.prebuilt.tool_node import ToolNode
    from langgraph.typing import ContextT


def create_react_agent(
    model: str | BaseChatModel,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]] = (),
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | None = None,
    state_schema: type[AgentState[ResponseT]] | None = None,
    context_schema: type[ContextT] | None = None,
    use_sequential_tools: bool = False,
    checkpointer: Any | None = None,
) -> CompiledStateGraph[AgentState[ResponseT], ContextT, _InputAgentState, _OutputAgentState[ResponseT]]:
    """Create a React agent with the given configuration.

    Args:
        model: The language model to use
        tools: List of tools available to the agent
        system_prompt: System prompt for the agent
        middleware: Middleware to apply
        response_format: Response format specification
        state_schema: Custom state schema
        context_schema: Custom context schema
        use_sequential_tools: If True, tools are executed sequentially (needed for DB session safety)
        checkpointer: Optional persistence checkpointer
    """
    model_instance = init_chat_model(model) if isinstance(model, str) else model
    system_message = SystemMessage(content=system_prompt) if isinstance(system_prompt, str) else system_prompt

    initial_response_format, tool_strategy_for_setup = convert_response_format(response_format)
    structured_output_tools = create_structured_output_tools(tool_strategy_for_setup)

    middleware_w_wrap_tool_call, middleware_w_awrap_tool_call = collect_middleware_with_tool_wrappers(middleware)
    wrap_tool_call_wrapper, awrap_tool_call_wrapper = create_tool_wrappers(
        middleware_w_wrap_tool_call, middleware_w_awrap_tool_call
    )

    tool_node, default_tools = setup_tools(
        tools, middleware, wrap_tool_call_wrapper, awrap_tool_call_wrapper, use_sequential_tools
    )

    validate_middleware(middleware)
    middleware_by_hook = collect_middleware_by_hook(middleware)
    wrap_model_call_handler, awrap_model_call_handler = create_model_call_handlers(
        middleware_by_hook["wrap_model_call"], middleware_by_hook["awrap_model_call"]
    )

    resolved_state_schema, input_schema, output_schema = resolve_state_schemas(middleware, state_schema)

    return build_react_agent_graph(
        model=model_instance,
        default_tools=default_tools,
        system_message=system_message,
        tool_node=tool_node,
        initial_response_format=initial_response_format,
        structured_output_tools=structured_output_tools,
        response_format=response_format,
        middleware_by_hook=middleware_by_hook,
        wrap_model_call_handler=wrap_model_call_handler,
        awrap_model_call_handler=awrap_model_call_handler,
        resolved_state_schema=resolved_state_schema,
        input_schema=input_schema,
        output_schema=output_schema,
        context_schema=context_schema,
        checkpointer=checkpointer,
    )


def build_react_agent_graph(
    *,
    # Model and tools
    model: BaseChatModel,
    default_tools: list[BaseTool | dict[str, Any]],
    system_message: SystemMessage | None,
    tool_node: ToolNode | None,
    # Response format
    initial_response_format: ToolStrategy | ProviderStrategy | AutoStrategy | None,
    structured_output_tools: dict[str, OutputToolBinding],
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | None,
    # Middleware
    middleware_by_hook: dict[str, list[AgentMiddleware]],
    wrap_model_call_handler: Callable | None,
    awrap_model_call_handler: Callable | None,
    # Schemas
    resolved_state_schema: type,
    input_schema: type,
    output_schema: type,
    context_schema: type[ContextT] | None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph[AgentState[ResponseT], ContextT, _InputAgentState, _OutputAgentState[ResponseT]]:
    """Build and compile the React agent graph."""
    graph = create_state_graph(resolved_state_schema, input_schema, output_schema, context_schema)

    add_model_node(
        graph,
        model,
        default_tools,
        system_message,
        initial_response_format,
        wrap_model_call_handler,
        awrap_model_call_handler,
        tool_node,
        structured_output_tools,
        None,
    )
    add_tool_node(graph, tool_node)
    add_middleware_nodes(graph, middleware_by_hook, resolved_state_schema)

    entry_node, loop_entry_node, loop_exit_node, exit_node = determine_routing_nodes(middleware_by_hook)

    add_start_edge(graph, entry_node)

    if tool_node is not None:
        add_tool_edges(
            graph,
            tool_node,
            loop_entry_node,
            loop_exit_node,
            exit_node,
            response_format,
            structured_output_tools,
        )
    elif len(structured_output_tools) > 0:
        add_structured_output_edges(graph, loop_entry_node, loop_exit_node, exit_node)
    else:
        add_simple_edge(graph, loop_exit_node, exit_node, middleware_by_hook, loop_entry_node)

    add_middleware_edges(graph, middleware_by_hook, loop_entry_node, exit_node)

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "create_react_agent",
    "build_react_agent_graph",
]
