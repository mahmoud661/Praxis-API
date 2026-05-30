"""Edge routing from tools node back to model node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from react_agent.utils.message_helpers import fetch_last_ai_and_tool_messages

if TYPE_CHECKING:
    from langchain.agents.structured_output import OutputToolBinding
    from langgraph.prebuilt.tool_node import ToolNode


def make_tools_to_model_edge(
    *,
    tool_node: ToolNode,
    model_destination: str,
    structured_output_tools: dict[str, OutputToolBinding],
    end_destination: str,
) -> Callable[[dict[str, Any]], str | None]:
    """Create an edge function that routes from tools to model node."""

    def tools_to_model(state: dict[str, Any]) -> str | None:
        last_ai_message, tool_messages = fetch_last_ai_and_tool_messages(state["messages"])
        # Defensive: a rewind that drops every AIMessage leaves this branch
        # with nothing to decide. Exit to END so the next invocation can
        # produce a fresh assistant turn from scratch.
        if last_ai_message is None:
            return end_destination

        # 1. Exit condition: All executed tools have return_direct=True
        # Filter to only client-side tools (provider tools are not in tool_node)
        client_side_tool_calls = [c for c in last_ai_message.tool_calls if c["name"] in tool_node.tools_by_name]
        if client_side_tool_calls and all(
            tool_node.tools_by_name[c["name"]].return_direct for c in client_side_tool_calls
        ):
            return end_destination

        # 2. Exit condition: A structured output tool was executed
        if any(t.name in structured_output_tools for t in tool_messages):
            return end_destination

        # 3. Default: Continue the loop
        #    Tool execution completed successfully, route back to the model
        #    so it can process the tool results and decide the next action.
        return model_destination

    return tools_to_model
