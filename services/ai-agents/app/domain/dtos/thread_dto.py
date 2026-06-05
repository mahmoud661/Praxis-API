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
class ThreadConfigView:
    """Per-thread overrides on top of the agent's default capability state.

    `agent_id` (`None` → use the account default agent), `tool_overrides`
    (`{tool_id: enabled_bool}` — sparse, only entries for tools the user
    actually flipped), `custom_system_prompt_id` (future use).

    Stored as a JSON sub-object inside the LangGraph k/v store value
    alongside owner_id + title + timestamps. Threads created BEFORE
    config rolled out have this set to `EMPTY_CONFIG` on read.
    """

    agent_id: str | None = None
    tool_overrides: dict[str, bool] = field(default_factory=dict)
    custom_system_prompt_id: str | None = None


# A reusable "no overrides" sentinel for older threads.
EMPTY_CONFIG = ThreadConfigView()


@dataclass(frozen=True, slots=True)
class ThreadView:
    id: str
    owner_id: str
    title: str
    created_at: str  # ISO-8601
    updated_at: str  # ISO-8601
    config: ThreadConfigView = field(default_factory=ThreadConfigView)


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
