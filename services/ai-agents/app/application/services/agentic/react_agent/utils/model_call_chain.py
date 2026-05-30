"""Model call handler composition for middleware chains.

Chains middleware model call handlers into a single composed handler.
Normalizes return values to ModelResponse format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Sequence

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage


def normalize_to_model_response(result: "ModelResponse | AIMessage") -> "ModelResponse":
    """Normalize middleware return value to ModelResponse."""
    from langchain.agents.middleware.types import ModelResponse
    from langchain_core.messages import AIMessage

    if isinstance(result, AIMessage):
        return ModelResponse(result=[result], structured_response=None)
    return result


def chain_model_call_handlers(
    handlers: Sequence[
        Callable[
            ["ModelRequest", Callable[["ModelRequest"], "ModelResponse"]],
            "ModelResponse | AIMessage",
        ]
    ],
) -> Callable[["ModelRequest", Callable[["ModelRequest"], "ModelResponse"]], "ModelResponse",] | None:
    """Compose model call handlers into middleware stack (first = outermost).

    Args:
        handlers: Model call handlers in middleware order.

    Returns:
        Composed handler, or `None` if empty.

    Example:
        handler = chain_model_call_handlers([auth, cache, retry])
        # Request flows: auth -> cache -> retry -> model
        # Response flows: model -> retry -> cache -> auth
    """

    if not handlers:
        return None

    if len(handlers) == 1:
        # Single handler - wrap to normalize output
        single_handler = handlers[0]

        def normalized_single(
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse],
        ) -> ModelResponse:
            result = single_handler(request, handler)
            return normalize_to_model_response(result)

        return normalized_single

    def compose_two(
        outer: Callable[
            [ModelRequest, Callable[[ModelRequest], ModelResponse]],
            ModelResponse | AIMessage,
        ],
        inner: Callable[
            [ModelRequest, Callable[[ModelRequest], ModelResponse]],
            ModelResponse | AIMessage,
        ],
    ) -> Callable[[ModelRequest, Callable[[ModelRequest], ModelResponse]], ModelResponse,]:
        """Compose two handlers where outer wraps inner."""

        def composed(
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse],
        ) -> ModelResponse:
            # Create a wrapper that calls inner with the base handler and normalizes
            def inner_handler(req: ModelRequest) -> ModelResponse:
                inner_result = inner(req, handler)
                return normalize_to_model_response(inner_result)

            # Call outer with the wrapped inner as its handler and normalize
            outer_result = outer(request, inner_handler)
            return normalize_to_model_response(outer_result)

        return composed

    # Compose right-to-left: outer(inner(innermost(handler)))
    result = handlers[-1]
    for handler in reversed(handlers[:-1]):
        result = compose_two(handler, result)

    # Wrap to ensure final return type is exactly ModelResponse
    def final_normalized(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # result here is typed as returning ModelResponse | AIMessage but compose_two normalizes
        final_result = result(request, handler)
        return normalize_to_model_response(final_result)

    return final_normalized


def chain_async_model_call_handlers(
    handlers: Sequence[
        Callable[
            [ModelRequest, Callable[[ModelRequest], Awaitable[ModelResponse]]],
            Awaitable[ModelResponse | AIMessage],
        ]
    ],
) -> Callable[[ModelRequest, Callable[[ModelRequest], Awaitable[ModelResponse]]], Awaitable[ModelResponse],] | None:
    """Compose async model call handlers into middleware stack (first = outermost).

    Args:
        handlers: Async model call handlers in middleware order.

    Returns:
        Composed async handler, or `None` if empty.
    """

    if not handlers:
        return None

    if len(handlers) == 1:
        # Single handler - wrap to normalize output
        single_handler = handlers[0]

        async def normalized_single(
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            result = await single_handler(request, handler)
            return normalize_to_model_response(result)

        return normalized_single

    def compose_two(
        outer: Callable[
            [ModelRequest, Callable[[ModelRequest], Awaitable[ModelResponse]]],
            Awaitable[ModelResponse | AIMessage],
        ],
        inner: Callable[
            [ModelRequest, Callable[[ModelRequest], Awaitable[ModelResponse]]],
            Awaitable[ModelResponse | AIMessage],
        ],
    ) -> Callable[[ModelRequest, Callable[[ModelRequest], Awaitable[ModelResponse]]], Awaitable[ModelResponse],]:
        """Compose two async handlers where outer wraps inner."""

        async def composed(
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            # Create a wrapper that calls inner with the base handler and normalizes
            async def inner_handler(req: ModelRequest) -> ModelResponse:
                inner_result = await inner(req, handler)
                return normalize_to_model_response(inner_result)

            # Call outer with the wrapped inner as its handler and normalize
            outer_result = await outer(request, inner_handler)
            return normalize_to_model_response(outer_result)

        return composed

    # Compose right-to-left: outer(inner(innermost(handler)))
    result = handlers[-1]
    for handler in reversed(handlers[:-1]):
        result = compose_two(handler, result)

    # Wrap to ensure final return type is exactly ModelResponse
    async def final_normalized(
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # result here is typed as returning ModelResponse | AIMessage but compose_two normalizes
        final_result = await result(request, handler)
        return normalize_to_model_response(final_result)

    return final_normalized
