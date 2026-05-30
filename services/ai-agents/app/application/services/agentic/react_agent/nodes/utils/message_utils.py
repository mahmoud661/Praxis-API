"""Utilities for message processing and slicing."""

from typing import List, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from react_agent.nodes.utils.interrupt_message import InterruptMessage

MAX_RECENT_MESSAGES_TO_KEEP = 10


def trim_to_summary_context(state: dict, target_messages: int = MAX_RECENT_MESSAGES_TO_KEEP) -> List[AnyMessage]:
    """
    Construct a context view for the model call:
        [Summary] + [Recent Messages]

    When the state flag `preserve_first_human_message=True` is set (subagent contexts),
    the view becomes:
        [First HumanMessage] + [Summary] + [Recent Messages]

    This keeps the original task instruction visible at all times, even after many
    summarization rounds have pushed it past `last_covered_index`.
    """
    messages = state.get("messages", [])

    summary = state.get("summary")
    last_covered_index = state.get("last_covered_index", 0)

    # First, slice the messages (if summarized)
    # Safety: Ensure last_covered_index does not exceed length of messages
    effective_index = min(last_covered_index, len(messages)) if last_covered_index > 0 else 0
    recent_messages = messages[effective_index:] if messages else []

    # Filter out InterruptMessage and its variants - these are for UI/human tracking only
    valid_tool_call_ids = set()
    filtered_recent = []
    for m in recent_messages:
        # Check if it's an explicit InterruptMessage object
        if isinstance(m, InterruptMessage):
            continue

        # Check message role
        if getattr(m, "role", None) == "interrupt":
            continue

        # Check additional_kwargs
        addl = getattr(m, "additional_kwargs", {}) or {}
        if addl.get("message_type") == "interrupt":
            continue

        # Track valid tool calls to prevent orphaned ToolMessages
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if isinstance(tc, dict) and tc.get("id"):
                    valid_tool_call_ids.add(tc.get("id"))

        # Handle orphaned ToolMessages
        if isinstance(m, ToolMessage):
            if getattr(m, "tool_call_id", None) not in valid_tool_call_ids:
                # Convert orphaned tool message to system message so the context is preserved
                # without violating the provider's API constraints.
                filtered_recent.append(
                    SystemMessage(content=f"<tool_output name=\"{m.name or 'unknown'}\">\n{m.content}\n</tool_output>")
                )
                continue

        filtered_recent.append(m)

    if summary and isinstance(summary, (HumanMessage, SystemMessage, str)):
        # Normalise: always expose the summary to the model as a HumanMessage
        if isinstance(summary, str):
            summary_msg = HumanMessage(content=summary)
        elif isinstance(summary, SystemMessage):
            summary_msg = HumanMessage(content=summary.content)
        else:
            summary_msg = summary

        # Only subagent contexts opt-in to preserving the first HumanMessage.
        # This ensures the original task instruction is never lost across summarization rounds.
        if state.get("preserve_first_human_message", False):
            first_human: Optional[AnyMessage] = next((m for m in messages if isinstance(m, HumanMessage)), None)
            if (
                first_human is not None
                and last_covered_index > 0
                and first_human is not summary_msg
                # Guard: don't duplicate if it's already the first message in the recent window
                and (not filtered_recent or filtered_recent[0] is not first_human)
            ):
                return [first_human, summary_msg] + filtered_recent

        return [summary_msg] + filtered_recent

    # If the list is empty after filtering (e.g., first message was an interrupt),
    # we must provide at least one valid message to the model to avoid a 400/500 crash.
    if not filtered_recent:
        # Check if we have any original messages to get content from, otherwise use a default
        content = "Continue"
        for m in reversed(messages):
            if hasattr(m, "content") and m.content:
                # If it's a list (complex content), extract text
                if isinstance(m.content, list):
                    text_parts = [
                        item.get("text", "")
                        for item in m.content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    if text_parts:
                        content = "".join(text_parts)
                        break
                elif isinstance(m.content, str):
                    content = m.content
                    break
        return [HumanMessage(content=content)]

    return filtered_recent
