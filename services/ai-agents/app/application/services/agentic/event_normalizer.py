"""
LangChain `astream_events(v2)` → clean wire envelope.

Why this layer exists: LangChain serializes message objects through
`langchain_core.load.dump.dumpd()` into a polymorphic `{lc, type, kwargs}`
shape designed for round-trip deserialization. The frontend doesn't care
about round-tripping — it wants `chunk.content`, `chunk.tool_calls`, etc.
flat. We normalize at the backend boundary so the frontend protocol stays
small and well-defined, and the LangChain version doesn't leak into JS.

Wire envelope (what `normalize_event` returns):

    {
      "event":   "on_chat_model_stream",   # original LC event name
      "name":    "ChatOpenAI",             # node/model name (for filtering)
      "run_id":  "abc-123",                # LC run id (correlate streams)
      "data":    { ... event-specific }
    }

Event-specific payloads:

    on_chat_model_stream:
      data: { "chunk": <Message> }

    on_chat_model_end:
      data: { "output": <Message> }

    on_tool_start:
      data: { "input": <args dict> }

    on_tool_end:
      data: { "output": <result>, "name": <tool_name> }

    on_chat_model_start:
      data: {}                              # ack only; lets the UI clear "thinking"

Anything else → returned as `None` (filtered out). The big noisy ones
(`on_chain_*`, `on_parser_*`) flood the wire with no UI value.

<Message> shape:

    {
      "id": "...",
      "role": "assistant",                  # normalized
      "content": "text" | [ContentBlock],   # str OR list of blocks
      "tool_calls": [ToolCall],             # finalised; on AIMessage (end)
      "tool_call_chunks": [ToolCallChunk],  # streaming deltas; on AIMessageChunk
      "additional_kwargs": { ... }          # provider metadata (reasoning etc.)
    }

ContentBlock — kept as-is from LangChain (Anthropic-style):
    { "type": "text",     "text": "..." }
    { "type": "thinking", "thinking": "..." }     # Anthropic extended thinking
    { "type": "reasoning","reasoning": "..." }    # OpenAI reasoning models
"""

from __future__ import annotations

from typing import Any

# Events we drop on the floor — they're useful for tracing in the dev console
# but offer nothing the chat UI can render, and they outnumber model events
# 100:1 in a typical run.
_DROPPED = {
    "on_chain_start",
    "on_chain_end",
    "on_chain_stream",
    "on_parser_start",
    "on_parser_end",
    "on_parser_stream",
    "on_prompt_start",
    "on_prompt_end",
    "on_retriever_start",
    "on_retriever_end",
    "on_llm_start",
    "on_llm_end",
    "on_llm_stream",
}


# Tags that mark internal / infrastructure LLM calls that must never reach
# the chat UI. Any event whose `tags` list contains one of these is dropped.
_INTERNAL_TAGS = {"no_stream", "no_ui", "internal"}


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one LC event into the wire envelope, or `None` if the event
    should be filtered."""
    name = raw.get("event")
    if not name or name in _DROPPED:
        return None

    # Drop events from internal infrastructure nodes (compaction, subagents,
    # summarization). These are tagged in the RunnableConfig that kicked them
    # off; LangGraph propagates config tags onto every child event.
    event_tags: list[str] = raw.get("tags") or []
    if _INTERNAL_TAGS.intersection(event_tags):
        return None

    out: dict[str, Any] = {
        "event": name,
        "name": raw.get("name"),
        "run_id": raw.get("run_id"),
    }

    data = raw.get("data") or {}

    if name == "on_chat_model_start":
        out["data"] = {}
        return out

    if name == "on_chat_model_stream":
        chunk = data.get("chunk")
        if chunk is None:
            return None
        out["data"] = {"chunk": _message_to_dict(chunk)}
        return out

    if name == "on_chat_model_end":
        output = data.get("output")
        if output is None:
            return None
        out["data"] = {"output": _message_to_dict(output)}
        return out

    if name == "on_tool_start":
        out["data"] = {
            "input": _coerce_jsonable(data.get("input")),
            "tool_name": raw.get("name"),
        }
        return out

    if name == "on_tool_end":
        out["data"] = {
            "output": _coerce_jsonable(data.get("output")),
            "tool_name": raw.get("name"),
        }
        return out

    # Pass-through for anything else (custom events, future LC additions).
    out["data"] = _coerce_jsonable(data)
    return out


# ---------------------------------------------------------------------------
# Internal: turn a LangChain Message object into a plain dict the frontend
# can consume without knowing about LangChain internals.
# ---------------------------------------------------------------------------


def _message_to_dict(msg: Any) -> dict[str, Any]:
    content = getattr(msg, "content", "")
    return {
        "id": getattr(msg, "id", None),
        "role": _role_for(msg),
        "content": _serialize_content(content),
        "tool_calls": _serialize_tool_calls(getattr(msg, "tool_calls", []) or []),
        "tool_call_chunks": _serialize_tool_calls(
            getattr(msg, "tool_call_chunks", []) or []
        ),
        "additional_kwargs": _coerce_jsonable(
            getattr(msg, "additional_kwargs", {}) or {}
        ),
    }


def _role_for(msg: Any) -> str:
    msg_type = getattr(msg, "type", None)
    if msg_type in ("human", "user"):
        return "user"
    if msg_type in ("ai", "AIMessageChunk"):
        return "assistant"
    if msg_type == "system":
        return "system"
    if msg_type == "tool":
        return "tool"
    return msg_type or "assistant"


def _serialize_content(content: Any) -> Any:
    """content can be a plain string (most models) or a list of content
    blocks (Anthropic, OpenAI reasoning models). Preserve the structure but
    coerce everything to JSON-safe primitives."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [
            {"type": "text", "text": b} if isinstance(b, str) else _coerce_jsonable(b)
            for b in content
        ]
    return str(content)


def _serialize_tool_calls(calls: Any) -> list[dict[str, Any]]:
    if not calls:
        return []
    out: list[dict[str, Any]] = []
    for c in calls:
        if isinstance(c, dict):
            out.append(_coerce_jsonable(c))
        else:
            out.append(
                _coerce_jsonable(
                    {
                        "id": getattr(c, "id", None),
                        "name": getattr(c, "name", None),
                        "args": getattr(c, "args", None),
                    }
                )
            )
    return out


def _coerce_jsonable(value: Any) -> Any:
    """Recursive best-effort coercion to JSON primitives. Handles dataclasses,
    pydantic models, ToolMessage objects, and generic objects via `str()`."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_jsonable(v) for v in value]
    # pydantic / dataclass / BaseMessage all expose model_dump or dict-like.
    for attr in ("model_dump", "dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return _coerce_jsonable(fn())
            except Exception:  # noqa: BLE001
                pass
    # Message objects (e.g. ToolMessage) — pull just the content.
    content = getattr(value, "content", None)
    if content is not None:
        return _coerce_jsonable(content)
    return str(value)
