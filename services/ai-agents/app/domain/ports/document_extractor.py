"""
Port for turning a file's raw bytes into model-ingestible content.

The extractor is the bridge between `IFileStorage` (bytes-in-bytes-out)
and the LLM. Two outputs the agent might want:

  - `extract_text(bytes, mime) -> str` for text-like inputs (PDF,
    plaintext, markdown, csv, json). The returned string drops into
    the model's context as ordinary text. Empty string means the
    extractor recognised the MIME but found no text (e.g. an image-
    only PDF) — the caller should fall back to a different strategy
    rather than feeding "" to the model.

  - `to_image_block(bytes, mime) -> dict` for image MIMEs. Returns a
    LangChain/Anthropic-shaped multimodal content block ready to slot
    into a `HumanMessage.content` list. Only meaningful for
    vision-capable models; the agent decides when to call it.

Unsupported MIMEs raise `UnsupportedMimeTypeError` so the caller can
surface a clean error to the model rather than feeding it junk.
Implementations live in `app/infrastructure/documents/`.
"""

from __future__ import annotations

from typing import Any, Protocol


class IDocumentExtractor(Protocol):
    def extract_text(self, *, data: bytes, mime_type: str) -> str:
        """Pull plain text from a text-bearing file. Returns the
        extracted string verbatim — no truncation, no summarization.
        Raises `UnsupportedMimeTypeError` for image / audio / video
        MIMEs (use `to_image_block` for images instead)."""

    def to_image_block(self, *, data: bytes, mime_type: str) -> dict[str, Any]:
        """Wrap an image's bytes as a multimodal content block.
        Returns the dict shape LangChain accepts inside a message's
        `content` list (Anthropic-style: `{type: image, source: {...}}`).
        Raises `UnsupportedMimeTypeError` for non-image MIMEs."""

    def supports(self, *, mime_type: str) -> bool:
        """True iff the extractor can handle this MIME via either
        `extract_text` or `to_image_block`. Callers query this to
        decide whether to attempt an extraction at all."""
