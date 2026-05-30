"""Middleware collection helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain.agents.middleware.types import StateT_co
    from langgraph.typing import ContextT


def collect_middleware_by_hook(
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]],
) -> dict[str, list[AgentMiddleware[StateT_co, ContextT]]]:
    """Collect middleware grouped by hook type.

    Args:
        middleware: Sequence of middleware instances

    Returns:
        Dictionary mapping hook names to lists of middleware implementing them

    Note:
        The same middleware instance may appear in multiple hook lists if it
        implements multiple hooks (e.g., before_agent + wrap_model_call + after_model).
        This is intentional - the node builder must deduplicate when iterating.
    """
    return {
        "before_agent": [
            m
            for m in middleware
            if m.__class__.before_agent is not AgentMiddleware.before_agent
            or m.__class__.abefore_agent is not AgentMiddleware.abefore_agent
        ],
        "before_model": [
            m
            for m in middleware
            if m.__class__.before_model is not AgentMiddleware.before_model
            or m.__class__.abefore_model is not AgentMiddleware.abefore_model
        ],
        "after_model": [
            m
            for m in middleware
            if m.__class__.after_model is not AgentMiddleware.after_model
            or m.__class__.aafter_model is not AgentMiddleware.aafter_model
        ],
        "after_agent": [
            m
            for m in middleware
            if m.__class__.after_agent is not AgentMiddleware.after_agent
            or m.__class__.aafter_agent is not AgentMiddleware.aafter_agent
        ],
        "wrap_model_call": [
            m
            for m in middleware
            if m.__class__.wrap_model_call is not AgentMiddleware.wrap_model_call
            or m.__class__.awrap_model_call is not AgentMiddleware.awrap_model_call
        ],
        "awrap_model_call": [
            m
            for m in middleware
            if m.__class__.awrap_model_call is not AgentMiddleware.awrap_model_call
            or m.__class__.wrap_model_call is not AgentMiddleware.wrap_model_call
        ],
    }


__all__ = ["collect_middleware_by_hook"]
