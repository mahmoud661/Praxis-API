"""History view must hide the synthetic preload plumbing.

`AttachmentPreloadMiddleware` fabricates an AIMessage with
`read_attachment` tool calls (ids prefixed `preload-`) plus paired
ToolMessages. They're model-facing only — the user already sees the
attachment as a chip on their own message, so the history view drops
the synthetic calls AND the now-empty carrier AIMessage. Real tool
calls on real assistant turns must keep rendering.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.application.services.threads_service import _pair_messages_for_view


def test_preload_pairs_are_hidden_real_tool_calls_stay() -> None:
    messages = [
        HumanMessage(content="summarize my file", id="h1"),
        # Synthetic preload pair — must vanish from the view.
        AIMessage(
            content="",
            id="a-preload",
            tool_calls=[
                {
                    "name": "read_attachment",
                    "id": "preload-abc123",
                    "args": {"file_id": "f1"},
                }
            ],
            additional_kwargs={"_preloaded_attachments": True},
        ),
        ToolMessage(
            content="[inline alias: turn0file1]\nfile body",
            name="read_attachment",
            tool_call_id="preload-abc123",
        ),
        # Real assistant turn with a real tool call — must stay.
        AIMessage(
            content="",
            id="a-real",
            tool_calls=[
                {"name": "kb_search", "id": "call_real1", "args": {"query": "x"}}
            ],
        ),
        ToolMessage(content="hits", name="kb_search", tool_call_id="call_real1"),
        AIMessage(content="Here's the summary.", id="a-answer"),
    ]

    views = _pair_messages_for_view(messages)

    ids = [v.id for v in views]
    assert "a-preload" not in ids  # carrier dropped entirely
    assert ids == ["h1", "a-real", "a-answer"]

    real = next(v for v in views if v.id == "a-real")
    assert [tc.name for tc in real.tool_calls] == ["kb_search"]
    assert real.tool_calls[0].result == "hits"


def test_mixed_real_and_preload_calls_on_one_message() -> None:
    # Defensive: if a preload call ever lands on a message that ALSO
    # has real calls, only the preload one is filtered.
    messages = [
        AIMessage(
            content="",
            id="a-mixed",
            tool_calls=[
                {
                    "name": "read_attachment",
                    "id": "preload-zzz",
                    "args": {"file_id": "f1"},
                },
                {"name": "kb_search", "id": "call_keep", "args": {}},
            ],
        ),
        ToolMessage(content="kb", name="kb_search", tool_call_id="call_keep"),
    ]
    views = _pair_messages_for_view(messages)
    assert len(views) == 1
    assert [tc.id for tc in views[0].tool_calls] == ["call_keep"]
