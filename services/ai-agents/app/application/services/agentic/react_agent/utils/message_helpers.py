"""Message extraction helpers for edge routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from langchain_core.messages import AIMessage, AnyMessage, ToolMessage


def fetch_last_ai_and_tool_messages(
    messages: list[AnyMessage],
) -> tuple[AIMessage | None, list[ToolMessage]]:
    """Extract the last AI message and subsequent tool messages from the
    message list.

    Returns ``(None, [])`` when the list has no ``AIMessage`` — callers
    (the conditional edges) must treat that as "agent loop has nothing
    to route" and exit. This case happens after a ``RemoveMessage`` based
    rewind that wipes every assistant turn, before the next agent
    invocation has had a chance to produce one.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    last_ai_index = -1
    last_ai_message: AIMessage | None = None

    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            last_ai_index = i
            last_ai_message = cast("AIMessage", messages[i])
            break

    if last_ai_message is None:
        return None, []

    tool_messages = [m for m in messages[last_ai_index + 1 :] if isinstance(m, ToolMessage)]
    return last_ai_message, tool_messages


__all__ = [
    "fetch_last_ai_and_tool_messages",
]
