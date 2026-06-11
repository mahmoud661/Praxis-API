"""
DocumentExtractor — concrete `IDocumentExtractor` covering:

  - Text-like MIMEs (text/*, application/json, csv, markdown) → utf-8
    decode of the bytes.
  - application/pdf → `pypdf.PdfReader` page-by-page text extraction.
    Empty page text comes back as "" (intentionally not None) so the
    caller can join pages with "\n\n" and not get nulls in the middle.
  - image/* → base64-encoded multimodal content block in the Anthropic-
    style shape `{type: "image", source: {type: "base64", ...}}`.
    LangChain's `HumanMessage.content` accepts this shape directly.

PDF extraction is synchronous and CPU-bound — kept off the event loop
by every async caller via `asyncio.to_thread`. Caller's responsibility,
not ours; we don't want to silently spawn threads from inside what
looks like a pure function.

`UnsupportedMimeTypeError` is raised for anything not in the supported
set so the caller surfaces "we can't read this" rather than feeding
random binary as text.

Auto-bound to the DI token `"IDocumentExtractor"`.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from pypdf import PdfReader

from ...application.services._errors import UnsupportedMimeTypeError


# Text-extractable MIMEs. Adding one is a one-line change here.
_TEXT_LIKE_MIMES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    }
)

_PDF_MIME = "application/pdf"

# Image MIMEs the multimodal content-block path handles. Kept in sync
# with `FilesService.ACCEPTED_MIME_TYPES`'s image bucket — any MIME the
# upload accepts as an image should be representable here.
_IMAGE_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)


class DocumentExtractor:
    """Auto-bound to the DI token `"IDocumentExtractor"`."""

    def extract_text(self, *, data: bytes, mime_type: str) -> str:
        if mime_type in _TEXT_LIKE_MIMES:
            return _decode_text(data)
        if mime_type == _PDF_MIME:
            return _extract_pdf_text(data)
        raise UnsupportedMimeTypeError(mime_type)

    def to_image_block(self, *, data: bytes, mime_type: str) -> dict[str, Any]:
        """Return a multimodal content block in OpenAI-vision format
        (`{type: "image_url", image_url: {url: "data:<mime>;base64,..."}}`).

        We use the OpenAI shape because the active chat client is
        `ChatOpenAI` pointed at the LiteLLM proxy — LiteLLM normalises
        OpenAI-style image blocks to whatever the upstream model
        expects (Anthropic vision, Gemini vision, etc.). Sending the
        Anthropic-native `{type: "image", source: {type: "base64",
        media_type, data}}` shape silently breaks here because
        ChatOpenAI doesn't translate it — the model receives nothing
        and reports "I didn't get any content back".
        """
        if mime_type not in _IMAGE_MIMES:
            raise UnsupportedMimeTypeError(mime_type)
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
        }

    def supports(self, *, mime_type: str) -> bool:
        return (
            mime_type in _TEXT_LIKE_MIMES
            or mime_type == _PDF_MIME
            or mime_type in _IMAGE_MIMES
        )


# ---- module helpers ----------------------------------------------------------


def _decode_text(data: bytes) -> str:
    """utf-8 with `errors="replace"` — we'd rather show garbled bytes
    than fail a model turn over a file with a stray 0x80."""
    return data.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes) -> str:
    """Page text joined by blank lines. `extract_text()` returns ""
    (not None) on a page with no text layer — image-only scans land
    here. The caller can detect "all pages empty" by checking the
    return value and falling back (e.g. OCR via vision model)."""
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)
