from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.constants import END

if TYPE_CHECKING:
    from langchain.agents.middleware.types import AgentMiddleware


def _get_first_middleware_node(middleware_list: list[AgentMiddleware], hook_name: str, default: str) -> str:
    """Get the first middleware node name for a hook, or default if empty."""
    return f"{middleware_list[0].name}.{hook_name}" if middleware_list else default


def _get_last_middleware_node(middleware_list: list[AgentMiddleware], hook_name: str, default: str) -> str:
    """Get the last middleware node name for a hook, or default if empty."""
    return f"{middleware_list[-1].name}.{hook_name}" if middleware_list else default


def determine_routing_nodes(
    middleware_by_hook: dict[str, list[AgentMiddleware]],
) -> tuple[str, str, str, str]:
    """Determine entry, loop entry, loop exit, and exit nodes.

    Returns:
        Tuple of (entry_node, loop_entry_node, loop_exit_node, exit_node)
    """
    before_agent = middleware_by_hook["before_agent"]
    before_model = middleware_by_hook["before_model"]
    after_model = middleware_by_hook["after_model"]
    after_agent = middleware_by_hook["after_agent"]

    entry_node = (
        _get_first_middleware_node(before_agent, "before_agent", "model")
        if before_agent
        else _get_first_middleware_node(before_model, "before_model", "model")
    )

    loop_entry_node = _get_first_middleware_node(before_model, "before_model", "model")
    loop_exit_node = _get_first_middleware_node(after_model, "after_model", "model")
    exit_node = _get_last_middleware_node(after_agent, "after_agent", END)

    return entry_node, loop_entry_node, loop_exit_node, exit_node


__all__ = [
    "determine_routing_nodes",
]
