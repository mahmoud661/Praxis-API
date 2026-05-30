"""LangGraph model node factories that integrate middleware chains."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from react_agent.nodes.utils import make_execute_model_async, make_execute_model_sync
from react_agent.nodes.utils.message_utils import trim_to_summary_context

MAX_RECENT_MESSAGES_TO_KEEP = 10
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from langchain.agents.middleware.types import AgentState
    from langchain.agents.structured_output import OutputToolBinding
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.prebuilt.tool_node import ToolNode
    from langgraph.runtime import Runtime
    from langgraph.typing import ContextT


def make_model_node(
    model: BaseChatModel,
    default_tools: list[BaseTool],
    system_message,
    initial_response_format,
    wrap_model_call_handler,
    tool_node: ToolNode | None,
    structured_output_tools: dict[str, OutputToolBinding],
    name: str | None,
):
    """Create a synchronous LangGraph model node with middleware support."""
    from langchain.agents.middleware.types import ModelRequest

    _execute_model_sync = make_execute_model_sync(tool_node, structured_output_tools, name)

    def model_node(
        state: AgentState,
        runtime: Runtime[ContextT],
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Sync model request handler with sequential middleware processing."""

        # Use smart context: [Latest Summary] + [Recent Unsummarized Messages]
        full_context = trim_to_summary_context(state, MAX_RECENT_MESSAGES_TO_KEEP)

        request = ModelRequest(
            model=model,
            tools=default_tools,
            system_message=system_message,
            response_format=initial_response_format,
            messages=full_context,
            tool_choice=None,
            state=state,
            runtime=runtime,
        )

        if wrap_model_call_handler is None:
            # No handlers - execute directly
            response = _execute_model_sync(request)
        else:
            # Call composed handler with base handler
            response = wrap_model_call_handler(request, _execute_model_sync)

        # Extract state updates from ModelResponse
        state_updates = {"messages": response.result}
        if response.structured_response is not None:
            state_updates["structured_response"] = response.structured_response

        return state_updates

    return model_node


def make_amodel_node(
    model: BaseChatModel,
    default_tools: list[BaseTool],
    system_message,
    initial_response_format,
    awrap_model_call_handler,
    tool_node: ToolNode | None,
    structured_output_tools: dict[str, OutputToolBinding],
    name: str | None,
):
    """Create an asynchronous LangGraph model node with middleware support."""
    from langchain.agents.middleware.types import ModelRequest

    _execute_model_async = make_execute_model_async(tool_node, structured_output_tools, name)

    async def amodel_node(
        state: AgentState,
        runtime: Runtime[ContextT],
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Async model request handler with sequential middleware processing."""

        # Use smart context: [Latest Summary] + [Recent Unsummarized Messages]
        full_context = trim_to_summary_context(state, MAX_RECENT_MESSAGES_TO_KEEP)

        request = ModelRequest(
            model=model,
            tools=default_tools,
            system_message=system_message,
            response_format=initial_response_format,
            messages=full_context,
            tool_choice=None,
            state=state,
            runtime=runtime,
        )

        if awrap_model_call_handler is None:
            # No async handlers - execute directly
            response = await _execute_model_async(request)
        else:
            # Call composed async handler with base handler
            response = await awrap_model_call_handler(request, _execute_model_async)

        # Extract state updates from ModelResponse
        state_updates = {"messages": response.result}
        if response.structured_response is not None:
            state_updates["structured_response"] = response.structured_response

        return state_updates

    return amodel_node
