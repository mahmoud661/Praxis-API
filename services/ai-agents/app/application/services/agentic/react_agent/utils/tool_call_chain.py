"""Tool call wrapper composition for middleware chains.

Chains middleware tool call wrappers into a single composed wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Sequence

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest, ToolCallWrapper
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command


def chain_tool_call_wrappers(
    wrappers: Sequence["ToolCallWrapper"],
) -> "ToolCallWrapper | None":
    """Compose wrappers into middleware stack (first = outermost).

    Args:
        wrappers: Wrappers in middleware order.

    Returns:
        Composed wrapper, or `None` if empty.

    Example:
        wrapper = chain_tool_call_wrappers([auth, cache, retry])
        # Request flows: auth -> cache -> retry -> tool
        # Response flows: tool -> retry -> cache -> auth
    """

    if not wrappers:
        return None

    if len(wrappers) == 1:
        return wrappers[0]

    def compose_two(outer: ToolCallWrapper, inner: ToolCallWrapper) -> ToolCallWrapper:
        """Compose two wrappers where outer wraps inner."""

        def composed(
            request: ToolCallRequest,
            execute: Callable[[ToolCallRequest], ToolMessage | Command],
        ) -> ToolMessage | Command:
            # Create a callable that invokes inner with the original execute
            def call_inner(req: ToolCallRequest) -> ToolMessage | Command:
                return inner(req, execute)

            # Outer can call call_inner multiple times
            return outer(request, call_inner)

        return composed

    # Chain all wrappers: first -> second -> ... -> last
    result = wrappers[-1]
    for wrapper in reversed(wrappers[:-1]):
        result = compose_two(wrapper, result)

    return result


def chain_async_tool_call_wrappers(
    wrappers: Sequence[
        Callable[
            [
                "ToolCallRequest",
                Callable[["ToolCallRequest"], Awaitable["ToolMessage | Command"]],
            ],
            Awaitable["ToolMessage | Command"],
        ]
    ],
) -> (
    Callable[
        [
            "ToolCallRequest",
            Callable[["ToolCallRequest"], Awaitable["ToolMessage | Command"]],
        ],
        Awaitable["ToolMessage | Command"],
    ]
    | None
):
    """Compose async wrappers into middleware stack (first = outermost).

    Args:
        wrappers: Async wrappers in middleware order.

    Returns:
        Composed async wrapper, or `None` if empty.
    """
    if not wrappers:
        return None

    if len(wrappers) == 1:
        return wrappers[0]

    def compose_two(
        outer: Callable[
            [
                ToolCallRequest,
                Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
            ],
            Awaitable[ToolMessage | Command],
        ],
        inner: Callable[
            [
                ToolCallRequest,
                Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
            ],
            Awaitable[ToolMessage | Command],
        ],
    ) -> Callable[
        [
            ToolCallRequest,
            Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        ],
        Awaitable[ToolMessage | Command],
    ]:
        """Compose two async wrappers where outer wraps inner."""

        async def composed(
            request: ToolCallRequest,
            execute: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        ) -> ToolMessage | Command:
            # Create an async callable that invokes inner with the original execute
            async def call_inner(req: ToolCallRequest) -> ToolMessage | Command:
                return await inner(req, execute)

            # Outer can call call_inner multiple times
            return await outer(request, call_inner)

        return composed

    # Chain all wrappers: first -> second -> ... -> last
    result = wrappers[-1]
    for wrapper in reversed(wrappers[:-1]):
        result = compose_two(wrapper, result)

    return result
