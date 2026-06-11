"""
Paragraph-boundary aware text chunker used by `KnowledgeService` to
split a document into overlapping chunks before embedding.

Lives in its own module because (a) it's a real algorithm with edge
cases worth isolating, (b) keeping it out of `knowledge_service.py`
makes that file purely about orchestration — read it once and you see
"extract → chunk → embed → upsert" without 60 lines of regex shuffling
in the middle.

Underscore-prefixed module name so the DI auto-discovery globber
(`auto_register` in `presentation/di/container.py`) skips it — it only
loads `*.py` files whose name doesn't start with `_`.
"""

from __future__ import annotations


# Target characters per chunk. 1200 chars ≈ 300 tokens for English —
# fits comfortably under the per-vector context limits of every
# embedding model the proxy is likely to be configured with, while
# being big enough that a single chunk usually carries a coherent
# idea. Tuned via gut, not benchmark; if recall feels off, lower it.
TARGET_CHARS = 1200

# Sliding-window overlap so an idea split across the boundary still
# lands in at least one chunk in full. 200/1200 ≈ 17% overlap is a
# common starting point in the RAG literature.
OVERLAP_CHARS = 200


def chunk_text(text: str) -> list[str]:
    """Split `text` into overlapping chunks of ~`TARGET_CHARS` chars.

    Algorithm: walk a window over the text; for each window, prefer to
    break on a paragraph boundary (`\\n\\n`) within the trailing 25%,
    then on a sentence boundary (`. `), and only fall back to a hard
    character cut if neither exists. The next window starts
    `OVERLAP_CHARS` before where the previous one ended.

    Returns at least one chunk for any non-empty input. Empty or
    whitespace-only input returns `[]`.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= TARGET_CHARS:
        return [text]

    chunks: list[str] = []
    cursor = 0
    n = len(text)
    while cursor < n:
        end = min(cursor + TARGET_CHARS, n)
        if end < n:
            end = _next_boundary(text, cursor, end)
        chunk = text[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        cursor = max(end - OVERLAP_CHARS, cursor + 1)
    return chunks


def _next_boundary(text: str, cursor: int, end: int) -> int:
    """Find a clean break point inside the trailing 25% of the window.
    Prefers paragraph, then sentence, then a hard cut at `end`."""
    window_start = cursor + int(TARGET_CHARS * 0.75)
    paragraph = text.rfind("\n\n", window_start, end)
    if paragraph != -1:
        return paragraph
    sentence = text.rfind(". ", window_start, end)
    if sentence != -1:
        return sentence + 2  # include the period + space
    return end  # hard cut
