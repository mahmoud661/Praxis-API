"""Multi-level compaction middleware for the AMS backend.

Blueprint: Claude Code's compaction pipeline
  https://github.com/emanueleielo/compact-middleware
Adapted for the AMS react_agent framework — no deepagents dependency.

Four-level cascade (levels 1-3 are free, level 4 costs one LLM call):

  Level 1 - COLLAPSE:     Group consecutive read/search tool calls into a badge summary
  Level 2 - TRUNCATE:     Shorten large tool-call args in old messages
  Level 3 - MICROCOMPACT: Clear stale tool results (time gap > threshold)
  Level 4 - SUMMARIZE:    9-section structured LLM summary (fires at token threshold)

State is NEVER written to LangGraph. All four levels operate on a deepcopy of
the message list and pass the compacted view to the model via wrap_model_call.
The raw LangGraph state keeps growing untouched.

Usage::

    from react_agent.middlewares.compaction_middleware import (
        CompactionMiddleware,
        CompactionConfig,
    )

    mw = CompactionMiddleware(
        model=llm,
        config=CompactionConfig(trigger=("fraction", 0.85), keep=("messages", 10)),
    )
    # pass mw to create_react_agent via middleware=[mw]
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

if TYPE_CHECKING:

    pass

logger = logging.getLogger(__name__)


ContextSize = tuple[str, int | float]


@dataclass
class CompactionConfig:
    """Master configuration for the compaction middleware.

    All defaults mirror Claude Code production values from compact-middleware.
    """

    trigger: ContextSize | list[ContextSize] | None = ("fraction", 0.85)
    keep: ContextSize = ("messages", 10)
    max_failures: int = 3

    collapse_enabled: bool = True
    collapse_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({"read_file", "grep", "glob", "web_search"})
    )
    collapse_min_group: int = 2

    truncate_enabled: bool = True
    truncate_trigger: ContextSize | None = ("fraction", 0.50)
    truncate_keep: ContextSize = ("messages", 20)
    truncate_max_chars: int = 2_000
    truncate_suffix: str = "...(truncated)"

    microcompact_enabled: bool = True
    microcompact_gap_minutes: float = 60.0
    microcompact_keep_recent: int = 5
    microcompact_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "read_file", "execute", "grep", "glob",
            "web_search", "web_fetch", "edit_file", "write_file",
        })
    )
    microcompact_placeholder: str = "[Old tool result cleared to save context]"

    custom_instructions: str | None = None


@dataclass
class _ThreadState:
    summary_message: HumanMessage | None = None
    cutoff_index: int = 0
    failures: int = 0


_IMAGE_TOKENS = 2_000


def _rough_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _rough_tokens_json(data: Any) -> int:
    try:
        s = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(data)
    return max(1, len(s) // 2)


def _estimate_message_tokens(msg: AnyMessage) -> int:
    content = msg.content
    if isinstance(content, str):
        return _rough_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, str):
                total += _rough_tokens(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    total += _rough_tokens(block.get("text", ""))
                elif btype in ("image", "document"):
                    total += _IMAGE_TOKENS
                elif btype == "tool_use":
                    total += _rough_tokens(block.get("name", ""))
                    total += _rough_tokens_json(block.get("input", { }))
                elif btype == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        total += _rough_tokens(inner)
                    elif isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "text":
                                total += _rough_tokens(ib.get("text", ""))
                else:
                    total += _rough_tokens_json(block)
        return total
    return _rough_tokens_json(content)


def _hybrid_token_count(messages: list[AnyMessage]) -> int:
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AIMessage) and msg.usage_metadata:
            it = msg.usage_metadata.get("input_tokens", 0) or 0
            ot = msg.usage_metadata.get("output_tokens", 0) or 0
            details = msg.usage_metadata.get("input_token_details", {}) or {}
            cache_read = details.get("cache_read", 0) or 0
            cache_write = details.get("cache_creation", 0) or 0
            total = it + ot + cache_read + cache_write
            if total > 0:
                tail = sum(_estimate_message_tokens(m) for m in messages[i + 1:])
                return total + tail
    return sum(_estimate_message_tokens(m) for m in messages)


def _should_trigger(
    messages: list[AnyMessage],
    total_tokens: int,
    trigger: ContextSize | list[ContextSize] | None,
    max_input_tokens: int | None,
) -> bool:
    if trigger is None:
        return False
    conditions = trigger if isinstance(trigger, list) else [trigger]
    for kind, value in conditions:
        if kind == "tokens" and total_tokens >= value:
            return True
        if kind == "messages" and len(messages) >= int(value):
            return True
        if kind == "fraction" and max_input_tokens is not None:
            threshold = int(max_input_tokens * float(value))
            if total_tokens >= max(1, threshold):
                return True
    return False


def _get_max_input_tokens(model) -> int | None:
    try:
        profile = getattr(model, "profile", None)
        if isinstance(profile, dict):
            v = profile.get("max_input_tokens")
            if isinstance(v, int):
                return v
    except Exception:
        # Best-effort introspection. `profile` is provider-specific and
        # any access can raise (attribute, key, type) — falling back to
        # the conservative default below is correct, not exceptional.
        pass
    return None


def _level1_collapse(
    messages: list[AnyMessage],
    collapse_tools: frozenset[str],
    min_group: int,
) -> list[AnyMessage]:
    if len(messages) < 2:
        return messages

    result: list[AnyMessage] = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        if not (isinstance(msg, AIMessage) and msg.tool_calls):
            result.append(msg)
            i += 1
            continue

        if not all(tc.get("name", "") in collapse_tools for tc in msg.tool_calls):
            result.append(msg)
            i += 1
            continue

        group = []
        while i < len(messages):
            ai_msg = messages[i]
            if not (isinstance(ai_msg, AIMessage) and ai_msg.tool_calls):
                break
            if not all(tc.get("name", "") in collapse_tools for tc in ai_msg.tool_calls):
                break
            tc_ids_needed = {tc.get("id") for tc in ai_msg.tool_calls if tc.get("id")}
            tool_results = []
            j = i + 1
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                tm = messages[j]
                tool_results.append(tm)
                tc_ids_needed.discard(tm.tool_call_id)
                j += 1
            if tc_ids_needed:
                break
            group.append((ai_msg, tool_results))
            i = j
        if len(group) < min_group:
            for ai_msg, tool_msgs in group:
                result.append(ai_msg)
                result.extend(tool_msgs)
            continue
        tool_counter: Counter[str] = Counter()
        for ai_msg, _ in group:
            for tc in ai_msg.tool_calls:
                tool_counter[tc.get("name", "unknown")] += 1
        parts = []
        read_n = tool_counter.get("read_file", 0)
        search_n = sum(tool_counter.get(t, 0) for t in ("grep", "glob", "web_search"))
        if read_n:
            parts.append(f"Read {read_n} file{'s' if read_n != 1 else ''}")
        if search_n:
            parts.append(f"Searched {search_n} time{'s' if search_n != 1 else ''}")
        for name, cnt in tool_counter.items():
            if name not in ("read_file", "grep", "glob", "web_search"):
                parts.append(f"{name} x{cnt}")
        badge = "[Collapsed: " + ", ".join(parts) + "]"
        result.append(HumanMessage(
            content=badge,
            additional_kwargs={"lc_source": "compaction_collapse"},
        ))
    return result


def _resolve_cutoff(
    messages: list[AnyMessage],
    keep: ContextSize,
    max_input_tokens: int | None,
) -> int:
    kind, value = keep
    if kind == "messages":
        return max(0, len(messages) - int(value))
    if kind == "fraction" and max_input_tokens is not None:
        target = int(max_input_tokens * float(value))
        acc = 0
        cutoff = len(messages)
        for idx in range(len(messages) - 1, -1, -1):
            acc += _estimate_message_tokens(messages[idx])
            if acc <= target:
                cutoff = idx
            else:
                break
        return cutoff
    return max(0, len(messages) - 20)


def _level2_truncate(
    messages: list[AnyMessage],
    total_tokens: int,
    config: CompactionConfig,
    max_input_tokens: int | None,
) -> list[AnyMessage]:
    if not config.truncate_enabled or config.truncate_trigger is None:
        return messages
    if not _should_trigger(messages, total_tokens, config.truncate_trigger, max_input_tokens):
        return messages
    cutoff = _resolve_cutoff(messages, config.truncate_keep, max_input_tokens)
    result = []
    for i, msg in enumerate(messages):
        if i >= cutoff or not isinstance(msg, AIMessage) or not msg.tool_calls:
            result.append(msg)
            continue
        new_tool_calls = []
        modified = False
        for tc in msg.tool_calls:
            new_args = {}
            tc_modified = False
            for k, v in tc.get("args", {}).items():
                if isinstance(v, str) and len(v) > config.truncate_max_chars:
                    new_args[k] = v[:config.truncate_max_chars] + config.truncate_suffix
                    tc_modified = True
                else:
                    new_args[k] = v
            new_tool_calls.append({**tc, "args": new_args} if tc_modified else tc)
            if tc_modified:
                modified = True
        result.append(msg.model_copy(update={"tool_calls": new_tool_calls}) if modified else msg)
    return result


def _get_last_ai_timestamp(messages: list[AnyMessage]):
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        for src in [msg.additional_kwargs, getattr(msg, "response_metadata", None) or {}]:
            if not isinstance(src, dict):
                continue
            ts = src.get("timestamp")
            if ts is None:
                continue
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts, tz=UTC)
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    pass
    return None


def _level3_microcompact(
    messages: list[AnyMessage],
    config: CompactionConfig,
) -> list[AnyMessage]:
    if not config.microcompact_enabled:
        return messages
    last_ts = _get_last_ai_timestamp(messages)
    if last_ts is None:
        return messages
    now = datetime.now(tz=UTC)
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=UTC)
    gap_minutes = (now - last_ts).total_seconds() / 60.0
    if gap_minutes < config.microcompact_gap_minutes:
        return messages
    compactable_ids = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tid = tc.get("id", "")
                tname = tc.get("name", "")
                if tid and tname in config.microcompact_tools:
                    compactable_ids[tid] = tname
    clearable_indices = []
    for idx, msg in enumerate(messages):
        if (
            isinstance(msg, ToolMessage)
            and compactable_ids.get(msg.tool_call_id or "")
            and not (
                isinstance(msg.response_metadata, dict)
                and msg.response_metadata.get("microcompacted")
            )
        ):
            clearable_indices.append(idx)
    keep = config.microcompact_keep_recent
    if keep >= len(clearable_indices):
        return messages
    to_clear = set(clearable_indices[:-keep] if keep > 0 else clearable_indices)
    result = list(messages)
    for idx in to_clear:
        msg = result[idx]
        if isinstance(msg, ToolMessage):
            result[idx] = ToolMessage(
                content=config.microcompact_placeholder,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                response_metadata={
                    **(msg.response_metadata or {}),
                    "microcompacted": True,
                },
            )
    return result


_NO_TOOLS_PREAMBLE = (
    "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\n"
    "- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.\n"
    "- You already have all the context you need in the conversation above.\n"
    "- Tool calls will be REJECTED and will waste your only turn.\n"
    "- Your entire response must be plain text.\n\n"
)

_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only. "
    "Tool calls will be rejected and you will fail the task."
)

_ANALYSIS_INSTRUCTION = (
    "Before providing your final summary, briefly analyze the conversation to ensure "
    "you cover all necessary points:\n\n"
    "1. Chronologically analyze each message. For each section identify:\n"
    "   - The user's explicit requests and intents\n"
    "   - Your approach to addressing the requests\n"
    "   - Key decisions, technical concepts, and code patterns\n"
    "   - Specific details: file names, code snippets, function signatures, file edits\n"
    "   - Errors encountered and how they were fixed\n"
    "   - Specific user feedback, especially if told to do something differently\n"
    "2. Double-check for technical accuracy and completeness."
)

_SECTIONS = (
    "1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail\n"
    "2. Key Technical Concepts: List all important techical concepts, technologies, and frameworks discussed.\n"
    "3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created.\n"
    "   Include full code snippets where applicable and a summary of why each file is important.\n"
    "4. Errors and fixes: List all errors encountered, how they were fixed, and specific user feedback.\n"
    "5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.\n"
    "6. All user messages: List ALL user messages that are not tool results.\n"
    "7. Pending Tasks: Outline any pending tasks explicitly requested.\n"
    "8. Current Work: Describe in detail what was being worked on immediately before this summary request.\n"
    "   Include file names and code snippets where applicable.\n"
    "9. Optional Next Step: The next step directly in line with the user's most recent explicit requests.\n"
    "   Include direct quotes from the most recent conversation showing exactly what task was in progress."
)


def _build_compact_prompt(custom_instructions: str | None = None) -> str:
    prompt = (
        _NO_TOOLS_PREAMBLE
        + "Your task is to create a detailed summary of the conversation so far, paying "
        "close attention to the user's explicit requests and your previous actions.\n"
        "This summary should be thorough in capturing technical details, code patterns, "
        "and architectural decisions essential for continuing work without losing context.\n\n"
        + _ANALYSIS_INSTRUCTION
        + "\n\nYour summary should include the following sections:\n\n"
        + _SECTIONS
        + "\n\nPlease provide your summary based on the conversation so far, following this "
        "structure and ensuring precision and thoroughness in your response."
    )
    if custom_instructions:
        prompt += "\n\n## Additional Instructions\n" + custom_instructions
    return prompt + _NO_TOOLS_TRAILER


def _strip_scratchpad(text: str) -> str:
    # Remove thinking scratchpad tags that some models emit
    patterns = [
        r"(?s)<antThinking>.*?</antThinking>",
        r"(?s)<thinking>.*?</thinking>",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text)
    return text.strip()


def _build_summarization_messages(
    messages_to_summarize: list,
    prompt: str,
) -> list:
    return [
        SystemMessage(content=prompt),
        HumanMessage(content="Please summarize the following conversation:"),
        *messages_to_summarize,
        HumanMessage(content="Now provide your structured summary following the sections above."),
    ]


def _generate_summary(model, messages: list, custom_instructions: str | None = None) -> str:
    prompt = _build_compact_prompt(custom_instructions)
    llm_messages = _build_summarization_messages(messages, prompt)
    response = model.invoke(llm_messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return _strip_scratchpad(raw)


async def _agenerate_summary(model, messages: list, custom_instructions: str | None = None) -> str:
    prompt = _build_compact_prompt(custom_instructions)
    llm_messages = _build_summarization_messages(messages, prompt)
    response = await model.ainvoke(llm_messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return _strip_scratchpad(raw)


def _determine_cutoff(messages: list[AnyMessage], keep: ContextSize) -> int:
    kind, value = keep
    if kind == "messages":
        base = max(0, len(messages) - int(value))
    else:
        base = max(0, len(messages) - 10)
    return _align_to_human_boundary(messages, base)


def _align_to_human_boundary(messages: list[AnyMessage], cutoff: int) -> int:
    """Snap `cutoff` BACKWARD to the nearest HumanMessage at or before
    it, so the kept tail (`messages[cutoff:]`) always starts at a user
    turn. Two invariants this protects:

      1. No orphan ToolMessage at the head of the tail — a tail opening
         with a ToolMessage whose parent AIMessage (carrying the
         tool_calls) got summarized away is rejected by OpenAI with
         "'tool' must be a response to a preceding 'tool_calls'".
      2. We never keep FEWER than `keep` messages — moving backward
         only ever keeps MORE, so the snap can't silently shrink the
         tail below the configured window (forward-snapping could).

    Correctness (a valid, non-split tail) outranks aggression: if the
    only HumanMessage is at index 0 (e.g. one giant tool loop), we
    return 0 and skip summarization this round rather than emit a tail
    that splits a tool pair."""
    for idx in range(min(cutoff, len(messages) - 1), -1, -1):
        if isinstance(messages[idx], HumanMessage):
            return idx
    return 0


def _build_effective_messages(
    raw_messages: list[AnyMessage],
    thread_state: _ThreadState,
) -> list[AnyMessage]:
    if thread_state.summary_message is not None and thread_state.cutoff_index > 0:
        return [thread_state.summary_message] + raw_messages[thread_state.cutoff_index:]
    return list(raw_messages)


def _get_thread_id(fallback: str) -> str:
    try:
        from langgraph.config import get_config as _gc
        cfg = _gc()
        tid = cfg.get("configurable", {}).get("thread_id")
        if tid is not None:
            return str(tid)
    except Exception:
        # `get_config()` raises when called outside a LangGraph run
        # (e.g. unit tests instantiating the middleware directly). The
        # fallback ID keeps logging useful without forcing every caller
        # to set up a graph context.
        pass
    return fallback


class CompactionMiddleware(AgentMiddleware):
    """Advanced multi-level compaction middleware.

    Ported from Claude Code's compaction pipeline via compact-middleware
    (https://github.com/emanueleielo/compact-middleware) -- adapted for the
    AMS react_agent framework with no deepagents dependency.

    The four cascade levels run in order on every model call:
      1. COLLAPSE:     Group consecutive read/search calls into badge summaries
      2. TRUNCATE:     Shorten large tool-call args in older messages
      3. MICROCOMPACT: Clear stale tool results (time gap > threshold)
      4. SUMMARIZE:    9-section LLM summary when token threshold is exceeded

    State contract
    --------------
    This middleware NEVER writes to LangGraph state. The raw message list in
    LangGraph keeps growing untouched. Compaction state (summary, cutoff,
    failure count) is stored on the middleware *instance* in a
    per-thread dict protected by a threading.Lock.

    The model always receives a compacted "effective view" built inside
    wrap_model_call/awrap_model_call via request.override(messages=...).
    """

    def __init__(
        self,
        model,
        *,
        config: CompactionConfig | None = None,
        max_input_tokens: int | None = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._config = config or CompactionConfig()
        # Explicit override first — proxy-fronted models (LiteLLM) hide
        # the upstream profile, so introspection returns None and every
        # `("fraction", ...)` trigger silently never fires. Callers that
        # know the real context window MUST pass it.
        self._max_input_tokens = max_input_tokens or _get_max_input_tokens(model)
        self._lock = threading.Lock()
        self._thread_states: dict[str, _ThreadState] = {}
        self._fallback_thread_id = f"session_{uuid.uuid4().hex[8:]}"

    def _get_thread_state(self, thread_id: str) -> _ThreadState:
        with self._lock:
            if thread_id not in self._thread_states:
                self._thread_states[thread_id] = _ThreadState()
            return self._thread_states[thread_id]

    def _save_summary(self, thread_id: str, summary_message: HumanMessage, cutoff_index: int) -> None:
        with self._lock:
            ts = self._thread_states.setdefault(thread_id, _ThreadState())
            ts.summary_message = summary_message
            ts.cutoff_index = cutoff_index
            ts.failures = 0

    def _record_failure(self, thread_id: str) -> int:
        with self._lock:
            ts = self._thread_states.setdefault(thread_id, _ThreadState())
            ts.failures += 1
            return ts.failures

    def _apply_levels_1_3(self, messages: list[AnyMessage], total_tokens: int) -> list[AnyMessage]:
        cfg = self._config
        if cfg.collapse_enabled:
            messages = _level1_collapse(messages, cfg.collapse_tools, cfg.collapse_min_group)
        messages = _level2_truncate(messages, total_tokens, cfg, self._max_input_tokens)
        messages = _level3_microcompact(messages, cfg)
        return messages

    def _compact(self, raw_messages, thread_id: str, thread_state: _ThreadState) -> list:
        effective = _build_effective_messages(raw_messages, thread_state)
        total_tokens = _hybrid_token_count(effective)
        effective = self._apply_levels_1_3(effective, total_tokens)
        total_tokens = _hybrid_token_count(effective)
        if not _should_trigger(effective, total_tokens, self._config.trigger, self._max_input_tokens):
            return effective
        if thread_state.failures >= self._config.max_failures:
            logger.warning("CompactionMiddleware: circuit breaker open for thread %s", thread_id)
            return effective
        cutoff = _determine_cutoff(raw_messages, self._config.keep)
        _, keep_val = self._config.keep
        msgs_to_summ = effective[:max(1, len(effective) - int(keep_val))]
        if not msgs_to_summ:
            return effective
        try:
            logger.info("CompactionMiddleware: Level 4 summarization for thread %s", thread_id)
            summary_text = _generate_summary(self._model, msgs_to_summ, self._config.custom_instructions)
            summary_msg = HumanMessage(
                content=f"[Conversation Summary]\n\n{summary_text}",
                additional_kwargs={"lc_source": "compaction_summary"},
            )
            self._save_summary(thread_id, summary_msg, cutoff)
            return [summary_msg] + raw_messages[cutoff:]
        except Exception as exc:
            failures = self._record_failure(thread_id)
            logger.error("CompactionMiddleware: Level 4 failed for thread %s (failure %d/%d): %s",
                         thread_id, failures, self._config.max_failures, exc, exc_info=True)
            return effective

    async def _acompact(self, raw_messages, thread_id: str, thread_state: _ThreadState) -> list:
        effective = _build_effective_messages(raw_messages, thread_state)
        total_tokens = _hybrid_token_count(effective)
        effective = self._apply_levels_1_3(effective, total_tokens)
        total_tokens = _hybrid_token_count(effective)
        if not _should_trigger(effective, total_tokens, self._config.trigger, self._max_input_tokens):
            return effective
        if thread_state.failures >= self._config.max_failures:
            logger.warning("CompactionMiddleware: circuit breaker open for thread %s", thread_id)
            return effective
        cutoff = _determine_cutoff(raw_messages, self._config.keep)
        _, keep_val = self._config.keep
        msgs_to_summ = effective[:max(1, len(effective) - int(keep_val))]
        if not msgs_to_summ:
            return effective
        try:
            logger.info("CompactionMiddleware: async Level 4 summarization for thread %s", thread_id)
            summary_text = await _agenerate_summary(self._model, msgs_to_summ, self._config.custom_instructions)
            summary_msg = HumanMessage(
                content=f"[Conversation Summary]\n\n{summary_text}",
                additional_kwargs={"lc_source": "compaction_summary"},
            )
            self._save_summary(thread_id, summary_msg, cutoff)
            return [summary_msg] + raw_messages[cutoff:]
        except Exception as exc:
            failures = self._record_failure(thread_id)
            logger.error("CompactionMiddleware: async Level 4 failed for thread %s (failure %d/%d): %s",
                         thread_id, failures, self._config.max_failures, exc, exc_info=True)
            return effective

    def wrap_model_call(self, request, handler):
        thread_id = _get_thread_id(self._fallback_thread_id)
        thread_state = self._get_thread_state(thread_id)
        raw_messages = list(request.state.get("messages", []))
        compacted = self._compact(raw_messages, thread_id, thread_state)
        return handler(request.override(messages=compacted))

    async def awrap_model_call(self, request, handler):
        thread_id = _get_thread_id(self._fallback_thread_id)
        thread_state = self._get_thread_state(thread_id)
        raw_messages = list(request.state.get("messages", []))
        compacted = await self._acompact(raw_messages, thread_id, thread_state)
        return await handler(request.override(messages=compacted))


__all__ = ["CompactionMiddleware", "CompactionConfig"]
