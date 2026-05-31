"""Middleware validation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain.agents.middleware.types import StateT_co
    from langgraph.typing import ContextT


def validate_middleware(
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]],
) -> None:
    """Validate middleware instances for duplicates.

    Args:
        middleware: Sequence of middleware instances

    Raises:
        AssertionError: If duplicate middleware instances are found
    """
    if len({m.name for m in middleware}) != len(middleware):
        msg = "Please remove duplicate middleware instances."
        raise AssertionError(msg)


__all__ = ["validate_middleware"]
