"""Routing helpers for edge resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain.agents.middleware.types import JumpTo


def resolve_jump(
    jump_to: JumpTo | None,
    *,
    model_destination: str,
    end_destination: str,
) -> str | None:
    """Resolve jump_to directive to actual destination.

    Args:
        jump_to: Jump directive from middleware ('model', 'end', 'tools', or None)
        model_destination: Destination name for 'model' jumps
        end_destination: Destination name for 'end' jumps

    Returns:
        Resolved destination node name, or None if no jump
    """
    if jump_to == "model":
        return model_destination
    if jump_to == "end":
        return end_destination
    if jump_to == "tools":
        return "tools"
    return None


__all__ = [
    "resolve_jump",
]
