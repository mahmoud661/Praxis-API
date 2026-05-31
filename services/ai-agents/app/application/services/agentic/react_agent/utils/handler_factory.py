"""Handler factory for model call handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from react_agent.utils.model_call_chain import (
    chain_async_model_call_handlers,
    chain_model_call_handlers,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain.agents.middleware.types import AgentMiddleware, StateT_co
    from langgraph.typing import ContextT


def create_model_call_handlers(
    middleware_w_wrap_model_call: list[AgentMiddleware[StateT_co, ContextT]],
    middleware_w_awrap_model_call: list[AgentMiddleware[StateT_co, ContextT]],
) -> tuple[Callable | None, Callable | None]:
    """Create composed model call handlers from middleware.

    Args:
        middleware_w_wrap_model_call: Middleware with sync model wrappers
        middleware_w_awrap_model_call: Middleware with async model wrappers

    Returns:
        Tuple of (wrap_model_call_handler, awrap_model_call_handler)
    """
    wrap_model_call_handler = None
    if middleware_w_wrap_model_call:
        sync_handlers = [m.wrap_model_call for m in middleware_w_wrap_model_call]
        wrap_model_call_handler = chain_model_call_handlers(sync_handlers)

    awrap_model_call_handler = None
    if middleware_w_awrap_model_call:
        async_handlers = [m.awrap_model_call for m in middleware_w_awrap_model_call]
        awrap_model_call_handler = chain_async_model_call_handlers(async_handlers)

    return wrap_model_call_handler, awrap_model_call_handler


__all__ = [
    "create_model_call_handlers",
]
