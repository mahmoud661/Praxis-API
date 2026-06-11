"""Unit tests for CompactionMiddleware's Level-4 cutoff alignment.

The kept tail after a summarization MUST start at a HumanMessage —
if it opens with a ToolMessage whose parent AIMessage was summarized
away, OpenAI rejects the request ("'tool' must be a response to a
preceding 'tool_calls'"). `_determine_cutoff` snaps forward to the
next user turn to guarantee that invariant.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.application.services.agentic.react_agent.middlewares.compaction_middleware import (
    _determine_cutoff,
)


def _tool_round(call_id: str) -> list:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "kb_search", "id": call_id, "args": {}}],
        ),
        ToolMessage(content="result", tool_call_id=call_id, name="kb_search"),
    ]


def test_cutoff_snaps_forward_to_human_boundary() -> None:
    # 2 user turns, each followed by a tool round + answer = 8 messages.
    messages = [
        HumanMessage(content="q1"),          # 0
        *_tool_round("c1"),                  # 1, 2
        AIMessage(content="a1"),             # 3
        HumanMessage(content="q2"),          # 4
        *_tool_round("c2"),                  # 5, 6
        AIMessage(content="a2"),             # 7
    ]
    # keep=6 → naive cutoff lands at index 2 (a ToolMessage). The
    # aligned cutoff must advance to index 4, the next HumanMessage.
    assert _determine_cutoff(messages, ("messages", 6)) == 4


def test_cutoff_already_on_human_stays_put() -> None:
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
        AIMessage(content="a2"),
    ]
    # keep=2 → naive cutoff is index 2, already a HumanMessage.
    assert _determine_cutoff(messages, ("messages", 2)) == 2


def test_cutoff_with_no_human_after_falls_back_unaligned() -> None:
    messages = [
        HumanMessage(content="q1"),
        *_tool_round("c1"),
        *_tool_round("c2"),
        AIMessage(content="a1"),
    ]
    # keep=2 → naive cutoff is index 4; no HumanMessage at/after it.
    # Fall back to the unaligned index rather than summarizing the
    # whole thread including the live turn.
    assert _determine_cutoff(messages, ("messages", 2)) == 4
