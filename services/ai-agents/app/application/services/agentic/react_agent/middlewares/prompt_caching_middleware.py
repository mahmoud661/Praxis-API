"""
PromptCachingMiddleware — stamps an Anthropic-style `cache_control:
{type: "ephemeral"}` marker on the LAST message of every model
request. LiteLLM passes this through to Anthropic upstreams, which
then serve subsequent requests with the same prefix from cache at
~10% of full input price.

Why ONE breakpoint, on the LAST message:
  Anthropic's inference engine treats `cache_control` as "cache
  everything BEFORE this point." Putting more than one marker burns
  KV pages at non-boundary positions for no gain — Claude Code's
  leaked-source writeup spells out this exact tuning. The whole
  conversation history up to (but not including) the new user turn
  becomes the cached prefix; the new turn invalidates only the
  trailing few tokens.

When the upstream isn't Anthropic (OpenAI, Gemini), LiteLLM silently
drops the `cache_control` field — no-op, no error. Safe to leave on
unconditionally.

Lives on `awrap_model_call`: that's the only hook where we control
the OUTGOING request shape just before it hits the model. Mutating
state's `messages` channel here would persist the marker; mutating
the request's `messages` doesn't (the request copy goes out + is
discarded).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# The literal Anthropic / LiteLLM expect inside a content block.
_CACHE_MARKER: dict[str, str] = {"type": "ephemeral"}


# ---- middleware --------------------------------------------------------------


class PromptCachingMiddleware(AgentMiddleware):
    """Stamps one cache breakpoint on each outgoing model request.
    No constructor args — pure transformation."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: "Callable[[ModelRequest], Awaitable[ModelResponse]]",
    ) -> ModelResponse:
        new_messages = _stamp_cache_breakpoint(list(request.messages))
        return await handler(request.override(messages=new_messages))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: "Callable[[ModelRequest], ModelResponse]",
    ) -> ModelResponse:
        new_messages = _stamp_cache_breakpoint(list(request.messages))
        return handler(request.override(messages=new_messages))


# ---- module helpers ----------------------------------------------------------


def _stamp_cache_breakpoint(messages: list[Any]) -> list[Any]:
    """Rewrite the LAST cacheable message so its trailing content
    block carries `cache_control`. Returns a new list (the caller
    gave us its own copy via `list(request.messages)`).

    Skips empty histories. If the last cacheable message has string
    content, we upgrade it to `[{type: text, text: <str>,
    cache_control: ephemeral}]`. If it's already a list of blocks,
    we mutate the trailing block in place — or append a sentinel
    block if every existing block already has cache_control set."""
    if not messages:
        return messages
    target_idx = _last_cacheable_index(messages)
    if target_idx < 0:
        return messages
    messages[target_idx] = _with_cache_control(messages[target_idx])
    return messages


def _last_cacheable_index(messages: list[Any]) -> int:
    """Skip past message types Anthropic doesn't accept cache_control
    on (e.g. SystemMessage handled separately by some providers). We
    target HumanMessage / AIMessage / ToolMessage — covers everything
    the react loop emits."""
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], (HumanMessage, AIMessage, ToolMessage)):
            return idx
    return -1


def _with_cache_control(msg: Any) -> Any:
    """Return a new message of the same type with cache_control
    stamped on its trailing content block."""
    content = msg.content
    if isinstance(content, str):
        new_content = [
            {"type": "text", "text": content, "cache_control": _CACHE_MARKER}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        last = new_content[-1]
        if isinstance(last, dict):
            stamped = dict(last)
            stamped["cache_control"] = _CACHE_MARKER
            new_content[-1] = stamped
        elif isinstance(last, str):
            new_content[-1] = {
                "type": "text",
                "text": last,
                "cache_control": _CACHE_MARKER,
            }
        else:
            # Unknown block shape — append a sentinel rather than risk
            # mangling whatever the provider expects.
            new_content.append(
                {"type": "text", "text": "", "cache_control": _CACHE_MARKER}
            )
    else:
        return msg  # empty / non-string non-list content; skip silently

    # Construct a fresh instance of the same class so additional_kwargs
    # / tool_calls / id all survive.
    kwargs: dict[str, Any] = {"content": new_content}
    for attr in ("id", "additional_kwargs", "name", "tool_call_id", "tool_calls"):
        value = getattr(msg, attr, None)
        if value is not None:
            kwargs[attr] = value
    return type(msg)(**kwargs)
