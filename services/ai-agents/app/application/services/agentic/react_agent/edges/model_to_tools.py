"""Edge routing from model node to tools node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from langgraph.prebuilt.tool_node import ToolCallWithContext
from langgraph.types import Send

from react_agent.edges.routing_helpers import resolve_jump
from react_agent.utils.message_helpers import fetch_last_ai_and_tool_messages

if TYPE_CHECKING:
    from langchain.agents.structured_output import OutputToolBinding


def make_model_to_tools_edge(
    *,
    model_destination: str,
    structured_output_tools: dict[str, OutputToolBinding],
    end_destination: str,
) -> Callable[[dict[str, Any]], str | list[Send] | None]:
    """Create an edge function that routes from model to tools node."""

    def model_to_tools(
        state: dict[str, Any],
    ) -> str | list[Send] | None:
        # 1. if there's an explicit jump_to in the state, use it
        if jump_to := state.get("jump_to"):
            return resolve_jump(
                jump_to,
                model_destination=model_destination,
                end_destination=end_destination,
            )

        last_ai_message, tool_messages = fetch_last_ai_and_tool_messages(state["messages"])
        # No AIMessage at all means the rewind path stripped everything
        # the model produced. Nothing for this branch to route — bail to
        # END and let the next fresh invocation pick up from the model.
        if last_ai_message is None:
            return end_destination
        tool_message_ids = [m.tool_call_id for m in tool_messages]

        # 2. if the model hasn't called any tools, exit the loop
        # this is the classic exit condition for an agent loop
        if len(last_ai_message.tool_calls) == 0:
            return end_destination

        pending_tool_calls = [
            c
            for c in last_ai_message.tool_calls
            if c["id"] not in tool_message_ids and c["name"] not in structured_output_tools
        ]

        # 3. if there are pending tool calls, jump to the tool node
        if pending_tool_calls:
            return [
                Send(
                    "tools",
                    ToolCallWithContext(
                        __type="tool_call_with_context",
                        tool_call=tool_call,
                        state=state,
                    ),
                )
                for tool_call in pending_tool_calls
            ]

        # 4. if there is a structured response, exit the loop
        if "structured_response" in state:
            return end_destination

        # 5. AIMessage has tool calls, but there are no pending tool calls
        # which suggests the injection of artificial tool messages. jump to the model node
        return model_destination

    return model_to_tools
