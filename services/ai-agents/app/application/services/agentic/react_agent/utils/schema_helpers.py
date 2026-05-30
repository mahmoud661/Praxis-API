"""Schema resolution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from react_agent.utils.schema_resolver import resolve_schema

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain.agents.middleware.types import AgentMiddleware, AgentState, ResponseT, StateT_co
    from langgraph.typing import ContextT


def resolve_state_schemas(
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]],
    state_schema: type[AgentState[ResponseT]] | None,
) -> tuple[type, type, type]:
    """Resolve state schemas from middleware and user-provided schema.

    Args:
        middleware: Sequence of middleware instances
        state_schema: User-provided state schema (optional)

    Returns:
        Tuple of (resolved_state_schema, input_schema, output_schema)
    """
    from langchain.agents.middleware.types import AgentState

    middleware_schemas = [m.state_schema for m in middleware]
    # Use provided state_schema if available, otherwise use base AgentState
    base_state = state_schema if state_schema is not None else AgentState

    # Create ordered list with base_state first to ensure it takes precedence
    # "First win" policy in resolve_schema means base_state overrides middleware
    all_schemas = [base_state] + middleware_schemas

    resolved_state_schema = resolve_schema(all_schemas, "StateSchema", None)
    input_schema = resolve_schema(all_schemas, "InputSchema", "input")
    output_schema = resolve_schema(all_schemas, "OutputSchema", "output")

    return resolved_state_schema, input_schema, output_schema


__all__ = [
    "resolve_state_schemas",
]
