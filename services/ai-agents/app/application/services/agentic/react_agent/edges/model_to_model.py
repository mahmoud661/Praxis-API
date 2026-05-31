"""Edge routing from model node back to model node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from langgraph.types import Send

from react_agent.edges.routing_helpers import resolve_jump

if TYPE_CHECKING:
    pass


def make_model_to_model_edge(
    *,
    model_destination: str,
    end_destination: str,
) -> Callable[[dict[str, Any]], str | list[Send] | None]:
    """Create an edge function that routes from model to model node."""

    def model_to_model(
        state: dict[str, Any],
    ) -> str | list[Send] | None:
        # 1. Priority: Check for explicit jump_to directive from middleware
        if jump_to := state.get("jump_to"):
            return resolve_jump(
                jump_to,
                model_destination=model_destination,
                end_destination=end_destination,
            )

        # 2. Exit condition: A structured response was generated
        if "structured_response" in state:
            return end_destination

        # 3. Default: Continue the loop, there may have been an issue
        #     with structured output generation, so we need to retry
        return model_destination

    return model_to_model
