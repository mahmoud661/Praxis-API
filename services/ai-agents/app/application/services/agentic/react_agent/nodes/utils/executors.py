"""Model executor factories that wrap domain logic with middleware support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolNode
    from langchain.agents.structured_output import OutputToolBinding

from react_agent.nodes.utils.binding import get_bound_model
from react_agent.nodes.utils.output_handler import handle_model_output


def _prepare_messages(request: ModelRequest):
    """Prepare messages from request."""
    messages = request.messages
    if request.system_message:
        messages = [request.system_message, *messages]
    return messages


def _get_config(request: ModelRequest):
    """Safely retrieve config from request runtime."""
    return getattr(request.runtime, "config", None) if hasattr(request, "runtime") else None


def _build_response(output, name: str | None, effective_response_format, structured_output_tools):
    """Build the final ModelResponse."""
    if name:
        output.name = name

    handled_output = handle_model_output(output, effective_response_format, structured_output_tools)
    return ModelResponse(
        result=handled_output["messages"],
        structured_response=handled_output.get("structured_response"),
    )


def make_execute_model_sync(
    tool_node: ToolNode | None,
    structured_output_tools: dict[str, OutputToolBinding],
    name: str | None,
):
    """Create a synchronous model executor function."""

    def _execute_model_sync(request: ModelRequest) -> ModelResponse:
        """Execute model and return response."""
        model_, effective_response_format = get_bound_model(request, tool_node, structured_output_tools)
        messages = _prepare_messages(request)
        config = _get_config(request)

        output = model_.invoke(messages, config=config)
        return _build_response(output, name, effective_response_format, structured_output_tools)

    return _execute_model_sync


def make_execute_model_async(
    tool_node: ToolNode | None,
    structured_output_tools: dict[str, OutputToolBinding],
    name: str | None,
):
    """Create an asynchronous model executor function."""

    async def _execute_model_async(request: ModelRequest) -> ModelResponse:
        """Execute model asynchronously and return response."""
        model_, effective_response_format = get_bound_model(request, tool_node, structured_output_tools)
        messages = _prepare_messages(request)
        config = _get_config(request)

        output = await model_.ainvoke(messages, config=config)
        return _build_response(output, name, effective_response_format, structured_output_tools)

    return _execute_model_async
