"""Edge routing for middleware jumps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph._internal._runnable import RunnableCallable

from react_agent.edges.routing_helpers import resolve_jump

if TYPE_CHECKING:
    from langchain.agents.middleware.types import (
        AgentState,
        ContextT,
        JumpTo,
        ResponseT,
        _InputAgentState,
        _OutputAgentState,
    )
    from langgraph.graph.state import StateGraph


def add_middleware_edge(
    graph: StateGraph[AgentState[ResponseT], ContextT, _InputAgentState, _OutputAgentState[ResponseT]],
    *,
    name: str,
    default_destination: str,
    model_destination: str,
    end_destination: str,
    can_jump_to: list[JumpTo] | None,
) -> None:
    """Add a conditional edge that supports middleware jump directives."""

    if can_jump_to:

        def jump_edge(state: dict[str, Any]) -> str:
            return (
                resolve_jump(
                    state.get("jump_to"),
                    model_destination=model_destination,
                    end_destination=end_destination,
                )
                or default_destination
            )

        destinations = [default_destination]

        if "end" in can_jump_to:
            destinations.append(end_destination)
        if "tools" in can_jump_to:
            destinations.append("tools")
        if "model" in can_jump_to and name != model_destination:
            destinations.append(model_destination)

        graph.add_conditional_edges(name, RunnableCallable(jump_edge, trace=False), destinations)

    else:
        graph.add_edge(name, default_destination)
