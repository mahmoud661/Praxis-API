"""
DTOs for the `/v1/threads/*` endpoints. The presentation layer maps these to
Pydantic response models; the application layer talks in dataclasses so it
stays framework-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CreateThreadInput:
    owner_id: str
    title: str = "New conversation"


@dataclass(frozen=True, slots=True)
class ThreadView:
    id: str
    owner_id: str
    title: str
    created_at: str  # ISO-8601
    updated_at: str  # ISO-8601


@dataclass(frozen=True, slots=True)
class HistoryToolCallView:
    """Tool call extracted from an AIMessage's `.tool_calls`, paired
    with its result when the matching ToolMessage shows up later in
    history."""

    id: str
    name: str
    args: dict
    result: str | None  # None until a ToolMessage with this id appears


@dataclass(frozen=True, slots=True)
class HistoryMessageView:
    # The LangChain BaseMessage UUID — stable across re-renders and what
    # the retry/edit endpoints use to identify the message to rewind to.
    id: str
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    # Tool calls owned by this message (assistant role only). Empty for
    # other roles. The pairing with their results happens server-side so
    # the frontend gets a complete picture instead of two messages it has
    # to stitch together.
    tool_calls: list[HistoryToolCallView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HistoryPageView:
    """One page of paginated thread history.

    `messages` are in chronological order (oldest first), same as the
    full history endpoint. `next_cursor` is the id of the oldest
    message in this page — pass it back as `before` to load the
    preceding page. `has_more` is true iff there are messages before
    the returned slice."""

    messages: list[HistoryMessageView]
    has_more: bool
    next_cursor: str | None
