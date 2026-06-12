"""Unit tests for CompactionMiddleware's Level-4 cutoff alignment.

The kept tail after a summarization MUST start at a HumanMessage —
if it opens with a ToolMessage whose parent AIMessage was summarized
away, OpenAI rejects the history ("'tool' must be a response to a
preceding 'tool_calls'"). `_determine_cutoff` snaps BACKWARD to the
nearest user turn at or before the window, so the tail is always a
valid boundary and is never shrunk below the configured keep window
(moving backward only keeps MORE).
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


def test_cutoff_snaps_back_to_human_boundary() -> None:
    # A tool round inside the most recent turn. keep=3 puts the naive
    # cutoff mid-round (on the tool-call AIMessage); aligning BACK to
    # the preceding HumanMessage keeps the whole turn intact and never
    # orphans the ToolMessage.
    messages = [
        HumanMessage(content="q0"),          # 0
        AIMessage(content="a0"),             # 1
        HumanMessage(content="q1"),          # 2
        *_tool_round("c1"),                  # 3 (AI tool-call), 4 (Tool)
        AIMessage(content="a1"),             # 5
    ]
    # keep=3 → naive cutoff = 6-3 = 3 (the tool-call AIMessage). Snap
    # back to the HumanMessage at index 2.
    assert _determine_cutoff(messages, ("messages", 3)) == 2


def test_cutoff_already_on_human_stays_put() -> None:
    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
        AIMessage(content="a2"),
    ]
    # keep=2 → naive cutoff is index 2, already a HumanMessage.
    assert _determine_cutoff(messages, ("messages", 2)) == 2


def test_cutoff_with_only_leading_human_returns_zero() -> None:
    # One giant tool loop under a single user turn — the only
    # HumanMessage is at index 0. Rather than emit a tail that splits a
    # tool pair, return 0 (skip summarization this round).
    messages = [
        HumanMessage(content="q1"),
        *_tool_round("c1"),
        *_tool_round("c2"),
        AIMessage(content="a1"),
    ]
    # keep=2 → naive cutoff = 4; no human between 4 and 0 except index 0.
    assert _determine_cutoff(messages, ("messages", 2)) == 0


def test_cutoff_keeps_at_least_keep_messages() -> None:
    # Long alternating thread: snapping back must keep >= keep messages
    # and land on a human turn.
    messages: list = []
    for k in range(8):
        messages.append(HumanMessage(content=f"h{k}"))
        messages.append(AIMessage(content=f"a{k}"))
    # 16 messages, keep=6 → naive cutoff 10 (H5). Already a human.
    cutoff = _determine_cutoff(messages, ("messages", 6))
    assert cutoff == 10
    assert isinstance(messages[cutoff], HumanMessage)
    assert len(messages) - cutoff >= 6
