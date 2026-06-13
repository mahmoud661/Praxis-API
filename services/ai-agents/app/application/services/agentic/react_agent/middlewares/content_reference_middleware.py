"""
ContentReferenceMiddleware — runs after the model returns an assistant
message, scans the text for `turn{N}{cat}{M}` and `citeturn...` aliases,
resolves each to a typed payload, and attaches the resulting list to
the message's `additional_kwargs["content_references"]`.

Why a middleware and not a service: this transformation is part of the
agent runtime's output pipeline. It belongs to the same lifecycle that
emits tool calls, streams chunks, and decides when to halt — not to
some external orchestrator that calls into the agent. Hooking
`awrap_model_call` puts it exactly where it belongs: in the model
response path, before the message lands in LangGraph state and before
`event_normalizer` ships it over the wire.

Flow:

  1. Model returns its `ModelResponse(result=[AIMessage, ...])`.
  2. For each AIMessage in `response.result` with non-empty text, we
     scan the content, hit the `ReferenceLookup` port to
     resolve every parsed alias, and stash the resolved references on
     the message's `additional_kwargs`.
  3. The unchanged response object is returned. LangGraph reduces the
     message into state, `event_normalizer.normalize_event` serializes
     `additional_kwargs` straight through, and the WS carries
     `content_references: list[dict]` to the frontend.

What this does NOT do:

  - It does not modify the text content. Aliases stay in the prose so
    the frontend can replace `[start_idx:end_idx]` spans with rich
    components. If a reference doesn't resolve, the literal alias
    passes through as ordinary text — graceful degradation.
  - It does not stream references per-chunk. References live on the
    completed message only; the streaming text shows raw aliases as
    the model emits them, the final message swaps them out. Matches
    how ChatGPT ships `content_references` (one shot on completion).

Thread + owner ids come from `request.runtime.config["configurable"]`
— RunManager populates that. If either is missing we skip resolution
entirely (logs at debug, doesn't raise) — better to surface the literal
prose than to fail a model turn over missing config.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config

from ..references import ReferenceLookup
from ..references import resolve as resolve_references

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ContentReferenceMiddleware(AgentMiddleware):
    """Post-model hook that attaches a `content_references` array to
    each assistant message's `additional_kwargs`.

    The lookup port is constructor-injected — the same instance is used
    for every model call this middleware sees. Typical wiring binds it
    once at agent setup time from the DI container.
    """

    def __init__(self, lookup: ReferenceLookup) -> None:
        super().__init__()
        self._lookup = lookup

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        thread_id, owner_id = _runtime_ids(request)
        if thread_id is None or owner_id is None:
            return response
        for msg in getattr(response, "result", None) or []:
            await _annotate_message(
                message=msg,
                lookup=self._lookup,
                thread_id=thread_id,
                owner_id=owner_id,
            )
        return response

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # Sync path exists only so the middleware works when an agent
        # is invoked through the sync executor. Production traffic
        # always goes through the async path; this is a thin shim that
        # forwards without resolving (resolution is async by design).
        return handler(request)


# ---- module helpers ----------------------------------------------------------


async def _annotate_message(
    *,
    message: Any,
    lookup: ReferenceLookup,
    thread_id: str,
    owner_id: str,
) -> None:
    if not isinstance(message, AIMessage):
        return
    text = _message_text(message)
    if not text:
        return
    refs = await resolve_references(
        text=text,
        lookup=lookup,
        thread_id=thread_id,
        owner_id=owner_id,
    )
    if not refs:
        return
    # `additional_kwargs` is a plain dict on AIMessage; mutating in
    # place is the documented way to attach provider/middleware
    # metadata. event_normalizer pulls this field verbatim into the
    # wire envelope, so the frontend receives the list as-is.
    extras = message.additional_kwargs or {}
    extras["content_references"] = [asdict(r) for r in refs]
    message.additional_kwargs = extras


def _message_text(message: AIMessage) -> str:
    """Pull the plain-text view of an AIMessage's content. Anthropic
    and reasoning-model messages can carry a list of content blocks;
    we concatenate the `text` blocks and ignore the rest (a `thinking`
    block isn't part of the user-facing prose and shouldn't be scanned
    for references)."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


def _runtime_ids(request: ModelRequest) -> tuple[str | None, str | None]:
    """Pull `thread_id` and `owner_id` out of the live RunnableConfig.

    Same accessor pattern as the preload/compaction middlewares:
    LangGraph's `Runtime` object does NOT expose `.config` — the
    per-run RunnableConfig lives in the `var_child_runnable_config`
    contextvar for the duration of the graph execution. (An earlier
    version read `request.runtime.config` through `getattr`, which
    silently returned None on every call and disabled resolution
    entirely — don't regress to that.)

    Missing either id means we're running outside a real chat run
    (e.g. an isolated test) — skip resolution rather than blow up."""
    del request  # ids come from the contextvar, not the request
    config = var_child_runnable_config.get()
    if not isinstance(config, dict):
        return None, None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    owner_id = configurable.get("owner_id")
    if not isinstance(thread_id, str) or not isinstance(owner_id, str):
        return None, None
    return thread_id, owner_id
